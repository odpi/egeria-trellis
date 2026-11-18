"""TR-51 (Rocky's newcomer run, 2026-11-18 15:25Z) and TR-50.

`python -m advisor.incremental_indexer --collection egeria_docs` logged 'relation "egeria_docs" does not exist' for
all 4257 files, said "+0 / -0 chunks" and exited 0. egeria_docs is disabled: it was split into egeria_concepts,
egeria_types and egeria_general.
- (a) the indexer refuses a disabled or unknown collection in words and exits non-zero, and exits non-zero when
  every file fails;
- (b) clone_repos.py's REPOS lists the split names for egeria-docs;
- TR-50: the phase lists name the split docs collections (and the templates), not the disabled egeria_docs.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from advisor import incremental_indexer as ii
from advisor.collection_config import (ALL_COLLECTIONS, REPLACED_BY, get_phase1_collections,
                                       get_phase2_collections)

PKG = Path(__file__).resolve().parents[2]


def test_a_disabled_collection_is_refused_naming_its_replacements(capsys):
    with pytest.raises(KeyError) as exc:
        ii.build_indexer("egeria_docs")
    assert exc.value.args[0] == ("Collection 'egeria_docs' is disabled: it was split into egeria_concepts, "
                                 "egeria_types, egeria_general. Index those instead.")


def test_main_exits_non_zero_for_disabled_and_unknown_collections(monkeypatch):
    said = []
    monkeypatch.setattr(ii.logger, "error", lambda m, *a, **k: said.append(m))
    assert ii.main(["--collection", "egeria_docs"]) == 1
    assert said[-1].startswith("Collection 'egeria_docs' is disabled") and not said[-1].startswith("'")
    assert ii.main(["--collection", "no_such_collection"]) == 1
    assert said[-1].startswith("Unknown collection 'no_such_collection'. Known: ")
    assert "egeria_docs" not in said[-1]                       # only enabled collections are offered


class _Ingester:
    """Fails every file the way Rocky's run did."""

    def __init__(self, fail=True):
        self.failed_files, self.fail = [], fail

    def ingest_file(self, path):
        if self.fail:
            self.failed_files.append((str(path), 'UndefinedTable: relation "egeria_docs" does not exist'))
            return 0, 0, []
        return 1, 3, ["id1", "id2", "id3"]


def _indexer(ingester):
    idx = ii.IncrementalIndexer.__new__(ii.IncrementalIndexer)
    idx.collection_name = "egeria_concepts"
    idx.ingester = ingester
    idx.vector_store = SimpleNamespace(delete_entities=lambda *a, **k: None)
    idx.tracker = SimpleNamespace(untrack_file=lambda *a: [], compute_file_hash=lambda p: "h",
                                  track_file=lambda *a, **k: None, update_metadata=lambda *a, **k: None)
    return idx


def _changes(n):
    return ii.ChangeSet(new_files=[Path(f"/r/doc{i}.md") for i in range(n)], modified_files=[], deleted_files=[],
                        unchanged_files=[])


def test_every_file_failing_is_a_failed_update_with_the_error():
    result = _indexer(_Ingester(fail=True)).apply_updates(_changes(4), dry_run=False)
    assert result.success is False
    assert result.error == ('every one of the 4 file(s) failed to index; first: UndefinedTable: relation '
                            '"egeria_docs" does not exist')


def test_files_that_index_are_a_success():
    result = _indexer(_Ingester(fail=False)).apply_updates(_changes(2), dry_run=False)
    assert result.success is True and result.chunks_added == 6


# ── (b) and TR-50 ────────────────────────────────────────────────────────────

def _clone_repos():
    spec = importlib.util.spec_from_file_location("clone_repos", PKG / "scripts" / "clone_repos.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_clone_repos_lists_the_split_docs_collections_and_the_templates():
    repos = _clone_repos().REPOS
    assert repos["egeria-docs"]["collections"] == list(REPLACED_BY["egeria_docs"])
    assert "egeria_templates" in repos["egeria-python"]["collections"]
    listed = {c for r in repos.values() for c in r["collections"]}
    assert all(ALL_COLLECTIONS[c].enabled for c in listed), "a repo lists a disabled collection"


def test_the_phase_lists_cover_every_enabled_collection_and_no_disabled_one():
    phased = {c.name for c in get_phase1_collections() + get_phase2_collections()}
    enabled = {n for n, c in ALL_COLLECTIONS.items() if c.enabled}
    assert phased == enabled
    assert "egeria_docs" not in phased
