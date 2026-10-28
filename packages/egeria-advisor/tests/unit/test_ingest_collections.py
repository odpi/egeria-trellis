"""
Tests for scripts/ingest_collections.py — the Phase 1 ingestion driver.

Background (trevor, fresh deployment, 2026-09-04): `ingest_collections.py
--phase 1` reported pyegeria Files 0 and pyegeria_cli Files 0 although it
had just logged "Estimated files: 280" / "100" for them, while pyegeria_drE
ingested fine. Nothing was wrong with the clone or the source paths. The
script decided "collection already exists" from
`collection.name in vector_store.list_collections()`:

  * the web app's startup hook provisions every collection table *empty*
    as soon as the container starts, so on a fresh box the tables exist
    before the first ingest runs, and an empty table was treated as done;
  * the check compared a *collection* name with *table* names, and
    `pyegeria_drE` is stored as the table `pyegeria_dre` — so drE never
    matched and always ingested, while `pyegeria`/`pyegeria_cli` always
    matched and were skipped, with the summary printing a bare "Files: 0".

Covers:
  - collection source resolution against the current upstream egeria-python
    layout (top-level pyegeria/ with core/, config/ ..., commands/,
    md_processing/, docs/, tests/) — every Phase 1 source path resolves and
    yields candidate files
  - an existing-but-empty collection is ingested into, not skipped
  - an existing, populated collection is skipped with a reason (and dropped
    with --force)
  - the mixed-case collection name resolves through collection_exists(),
    not list_collections()
  - a zero-file result carries a reason; the exit code is non-zero only when
    nothing at all was ingested
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from advisor import collection_sources
from advisor.collection_config import (
    PYEGERIA_CLI_COLLECTION,
    PYEGERIA_COLLECTION,
    PYEGERIA_DRE_COLLECTION,
    get_phase1_collections,
)

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ingest_collections.py"


@pytest.fixture(scope="module")
def ingest_script() -> ModuleType:
    """Import scripts/ingest_collections.py by path (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location("ingest_collections_under_test", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# Current upstream egeria-python layout (odpi/egeria-python main, 2026-09):
# a top-level `pyegeria/` package with sub-packages, plus commands/,
# md_processing/, docs/ and tests/ beside it.
_CURRENT_LAYOUT = {
    "pyegeria/__init__.py": "",
    "pyegeria/core/__init__.py": "",
    "pyegeria/core/client.py": "class Client:\n    def ping(self):\n        return 1\n",
    "pyegeria/config/__init__.py": "",
    "pyegeria/config/settings.py": "def load():\n    return {}\n",
    "pyegeria/omvs/glossary.py": "def get_term():\n    return None\n",
    "tests/__init__.py": "",
    "tests/test_client.py": "def test_ping():\n    assert True\n",
    "commands/__init__.py": "",
    "commands/cli/egeria.py": "def main():\n    pass\n",
    "commands/cat/list_terms.py": "def list_terms():\n    pass\n",
    "md_processing/__init__.py": "",
    "md_processing/v2/parser.py": "def parse():\n    pass\n",
    "docs/README.md": "# docs\n",
    "docs/gen.py": "def gen():\n    pass\n",
}


@pytest.fixture
def repos_dir(tmp_path, monkeypatch, ingest_script) -> Path:
    repos = tmp_path / "repos"
    repo = repos / "egeria-python"
    for rel, content in _CURRENT_LAYOUT.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    monkeypatch.setattr(collection_sources, "get_repos_dir", lambda: repos)
    return repos


# ---------------------------------------------------------------------------
# Source resolution for the current layout
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "collection",
    get_phase1_collections(),
    ids=[c.name for c in get_phase1_collections()],
)
def test_every_phase1_source_path_resolves_on_current_layout(ingest_script, repos_dir, collection):
    resolved = ingest_script.get_collection_source_paths(collection)
    expected = [repos_dir / "egeria-python" / rel for rel in collection.source_paths]
    assert resolved == expected, (
        f"{collection.name}: source_paths {collection.source_paths} do not all exist "
        f"in the current egeria-python layout"
    )
    patterns = ingest_script.get_file_patterns(collection)
    assert ingest_script.count_files(resolved, patterns) > 0


def test_pyegeria_resolution_reaches_nested_subpackages(ingest_script, repos_dir):
    resolved = ingest_script.get_collection_source_paths(PYEGERIA_COLLECTION)
    count = ingest_script.count_files(resolved, ["*.py"])
    # pyegeria/ (6 .py across core/ config/ omvs/) + tests/ (2)
    assert count == 8


def test_missing_repo_resolves_to_nothing(ingest_script, tmp_path, monkeypatch):
    monkeypatch.setattr(collection_sources, "get_repos_dir", lambda: tmp_path / "nowhere")
    assert ingest_script.get_collection_source_paths(PYEGERIA_CLI_COLLECTION) == []


# ---------------------------------------------------------------------------
# Existence check: empty auto-provisioned tables and mixed-case names
# ---------------------------------------------------------------------------

class _FakeStore:
    """Mimics PgVectorStore: tables are keyed by *table* name, and the
    mixed-case `pyegeria_drE` collection maps to the `pyegeria_dre` table."""

    _TABLE_MAP = {"pyegeria_drE": "pyegeria_dre"}

    def __init__(self, rows: dict[str, int]):
        self.rows = dict(rows)  # table name -> row count
        self.dropped: list[str] = []
        self.created: list[str] = []

    def _table(self, name):
        return self._TABLE_MAP.get(name, name)

    def connect(self):
        pass

    def list_collections(self):
        return sorted(self.rows)

    def collection_exists(self, name):
        return self._table(name) in self.rows

    def get_collection_stats(self, name):
        return {"name": self._table(name), "num_entities": self.rows.get(self._table(name), 0)}

    def delete_collection(self, name):
        self.dropped.append(name)
        self.rows.pop(self._table(name), None)

    def create_collection(self, collection_name, description=None, drop_if_exists=False):
        self.created.append(collection_name)
        self.rows.setdefault(self._table(collection_name), 0)

    def create_index(self, **kwargs):
        pass


class _FakeIngester:
    """Stands in for CodeIngester; `fail_all` makes every file fail like a swallowed per-file exception."""

    instances: list["_FakeIngester"] = []

    def __init__(self, collection_name, chunk_size=None, chunk_overlap=None):
        self.collection_name = collection_name
        self.failed_files: list[tuple[str, str]] = []
        self.seen: list[Path] = []
        _FakeIngester.instances.append(self)

    def failure_summary(self, limit=3):
        if not self.failed_files:
            return ""
        return f"{len(self.failed_files)} file(s) failed: {self.failed_files[0][1]}"

    def ingest_directory(self, dir_path, file_pattern, recursive=True,
                         batch_size=50, exclude_patterns=None):
        from advisor.ingest import path_is_excluded

        files = [
            f for f in dir_path.rglob(file_pattern)
            if not path_is_excluded(f, exclude_patterns, root=dir_path)
        ]
        self.seen.extend(files)
        ok = 0
        for f in files:
            if self.fail_all:
                self.failed_files.append((str(f), "RuntimeError: boom"))
            else:
                ok += 1
        return ok, ok * 2

    fail_all = False


@pytest.fixture
def wired(ingest_script, repos_dir, monkeypatch):
    """Wire the script to a fake store and fake ingester; returns a factory."""
    _FakeIngester.instances.clear()
    _FakeIngester.fail_all = False
    monkeypatch.setattr(ingest_script, "CodeIngester", _FakeIngester)
    monkeypatch.setattr(ingest_script, "_record_ingest_time", lambda *a, **k: None)

    def _wire(rows: dict[str, int]) -> _FakeStore:
        store = _FakeStore(rows)
        monkeypatch.setattr(ingest_script, "get_vector_store", lambda: store)
        return store

    return _wire


def test_existing_chunk_count_resolves_mixed_case_name(ingest_script):
    store = _FakeStore({"pyegeria_dre": 12})
    assert "pyegeria_drE" not in store.list_collections()  # the old check's blind spot
    assert ingest_script.existing_chunk_count(store, "pyegeria_drE") == 12
    assert ingest_script.existing_chunk_count(store, "pyegeria") is None


def test_empty_provisioned_table_is_ingested_not_skipped(ingest_script, wired):
    # The web app's startup hook created the table, empty, before the first ingest.
    store = wired({"pyegeria": 0, "pyegeria_cli": 0, "pyegeria_dre": 0})
    result = ingest_script.ingest_collection(PYEGERIA_COLLECTION)
    assert not result.skipped
    assert result.files == 8 and result.chunks == 16
    assert result.reason is None
    assert store.dropped == []


def test_populated_table_is_skipped_with_reason_unless_forced(ingest_script, wired):
    store = wired({"pyegeria_cli": 696})
    result = ingest_script.ingest_collection(PYEGERIA_CLI_COLLECTION)
    assert result.skipped and result.files == 0
    assert "696" in result.reason and "--force" in result.reason
    assert store.dropped == [] and _FakeIngester.instances == []

    forced = ingest_script.ingest_collection(PYEGERIA_CLI_COLLECTION, force=True)
    assert store.dropped == ["pyegeria_cli"]
    assert forced.files == 3 and not forced.skipped


def test_populated_mixed_case_collection_is_also_skipped(ingest_script, wired):
    # Before the fix drE was *always* re-ingested because the table is lowercase.
    wired({"pyegeria_dre": 1031})
    result = ingest_script.ingest_collection(PYEGERIA_DRE_COLLECTION)
    assert result.skipped and "1031" in result.reason


def test_all_files_failing_reports_reason_and_candidates(ingest_script, wired):
    wired({})
    _FakeIngester.fail_all = True
    result = ingest_script.ingest_collection(PYEGERIA_CLI_COLLECTION)
    assert result.files == 0 and not result.skipped
    assert result.candidates == 3
    assert "0 of 3 candidate files" in result.reason
    assert "RuntimeError: boom" in result.reason


def test_result_unpacks_as_files_chunks_tuple(ingest_script):
    files, chunks = ingest_script.IngestResult(collection="x", files=4, chunks=9)
    assert (files, chunks) == (4, 9)


# ---------------------------------------------------------------------------
# Exit code: non-zero only when nothing at all was ingested
# ---------------------------------------------------------------------------

def _r(ingest_script, name, files, reason=None, skipped=False):
    return ingest_script.IngestResult(collection=name, files=files, chunks=files * 2,
                                      reason=reason, skipped=skipped)


def test_exit_zero_when_some_collections_were_skipped(ingest_script):
    results = {
        "pyegeria": _r(ingest_script, "pyegeria", 0, "collection already holds 6464 chunks", True),
        "pyegeria_cli": _r(ingest_script, "pyegeria_cli", 0, "collection already holds 696 chunks", True),
        "pyegeria_drE": _r(ingest_script, "pyegeria_drE", 50),
    }
    assert ingest_script.exit_code_for(results) == 0


def test_exit_nonzero_when_nothing_ingested(ingest_script):
    results = {
        "pyegeria": _r(ingest_script, "pyegeria", 0, "0 of 280 candidate files ... failed"),
        "pyegeria_cli": _r(ingest_script, "pyegeria_cli", 0, "no source paths", True),
    }
    assert ingest_script.exit_code_for(results) == 1
    assert ingest_script.exit_code_for({}) == 1


def test_exit_zero_for_pure_dry_run(ingest_script):
    results = {"pyegeria": _r(ingest_script, "pyegeria", 0, "dry run — no ingestion performed", True)}
    assert ingest_script.exit_code_for(results) == 0


# ---------------------------------------------------------------------------
# exclude_patterns are actually applied
#
# Found live 2026-09-10 on the running box: egeria_general is configured as
# "all of site/docs EXCEPT concepts/ and types/", but nothing on the ingest
# path ever read collection.exclude_patterns — ingest_directory() did a bare
# rglob — so the 179 concept and 168 type files were ingested a second time
# into egeria_general, duplicating both specialized collections.
#
# The parsers in advisor/data_prep/ do accept exclude_patterns and match with
# PurePath.match(), which does not treat a leading "**" as a recursive
# wildcard before Python 3.13 — so "**/concepts/**" matches nothing there.
# path_is_excluded() uses fnmatch instead; test_leading_doublestar_* pins that.
# ---------------------------------------------------------------------------

from advisor.collection_config import EGERIA_GENERAL_COLLECTION  # noqa: E402
from advisor.ingest import path_is_excluded  # noqa: E402

_DOCS_LAYOUT = {
    "site/docs/index.md": "# index\n",
    "site/docs/guides/getting-started.md": "# start\n",
    "site/docs/guides/admin/config.md": "# config\n",
    "site/docs/concepts/asset.md": "# asset\n",
    "site/docs/concepts/glossary-term.md": "# term\n",
    "site/docs/types/0/0010-base-model.md": "# types\n",
    "site/docs/types/1/0110-actors.md": "# actors\n",
}


@pytest.fixture
def docs_repos_dir(tmp_path, monkeypatch, ingest_script) -> Path:
    repos = tmp_path / "repos"
    repo = repos / "egeria-docs"
    for rel, content in _DOCS_LAYOUT.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    monkeypatch.setattr(collection_sources, "get_repos_dir", lambda: repos)
    return repos


def test_leading_doublestar_pattern_matches_a_nested_directory(tmp_path):
    path = tmp_path / "site" / "docs" / "concepts" / "asset.md"
    assert path_is_excluded(path, ["**/concepts/**"])
    # The pathlib form these patterns were written for, and why they never fired.
    assert not path.match("**/concepts/**") or sys.version_info >= (3, 13)


def test_path_is_excluded_leaves_unmatched_paths_alone(tmp_path):
    path = tmp_path / "site" / "docs" / "guides" / "getting-started.md"
    assert not path_is_excluded(path, ["**/concepts/**", "**/types/**"])
    assert not path_is_excluded(path, [])
    assert not path_is_excluded(path, None)


def test_path_is_excluded_matches_relative_to_root(tmp_path):
    root = tmp_path / "repo"
    path = root / "node_modules" / "pkg" / "index.js"
    # No leading directory before node_modules once made relative to root.
    assert path_is_excluded(path, ["**/node_modules/**"], root=root)


def test_count_files_honours_exclusions(ingest_script, docs_repos_dir):
    docs = docs_repos_dir / "egeria-docs" / "site" / "docs"
    assert ingest_script.count_files([docs], ["*.md"]) == 7
    assert ingest_script.count_files(
        [docs], ["*.md"], EGERIA_GENERAL_COLLECTION.exclude_patterns
    ) == 3


def test_egeria_general_does_not_ingest_concepts_or_types(
    ingest_script, docs_repos_dir, monkeypatch
):
    _FakeIngester.instances.clear()
    _FakeIngester.fail_all = False
    monkeypatch.setattr(ingest_script, "CodeIngester", _FakeIngester)
    monkeypatch.setattr(ingest_script, "_record_ingest_time", lambda *a, **k: None)
    monkeypatch.setattr(ingest_script, "get_vector_store", lambda: _FakeStore({}))

    result = ingest_script.ingest_collection(EGERIA_GENERAL_COLLECTION)

    # index.md + guides/getting-started.md + guides/admin/config.md
    assert result.files == 3
    assert result.candidates == 3, "the estimate must match what is ingested"

    seen = {p.name for i in _FakeIngester.instances for p in i.seen}
    assert seen == {"index.md", "getting-started.md", "config.md"}
    assert "asset.md" not in seen and "0010-base-model.md" not in seen
