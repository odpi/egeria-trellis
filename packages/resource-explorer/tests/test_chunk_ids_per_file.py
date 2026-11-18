"""R0 of BRIEF-REINDEX-FROM-SCRATCH.md: chunk ids minted per file, and chat votes carried across a re-insert.

The project owner's Q8 (2026-11-10). Ids were the chunk's position in the insert batch, so one added chunk shifted every
later id and two refreshes could give different files' chunks the same id. Now `sha256(collection:file:index_in_file:text)`.
A vote (`chunk_feedback`, keyed `<collection>:<id>`) follows its chunk through a re-insert by identical text, narrowed to
the same file; what cannot be matched is kept and reported, never guessed or deleted.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from resource_explorer import chunk_votes
from resource_explorer.vector_store_pg import MultiCollectionStore, chunk_ids, record_inserts

C = "repo_markdown_docs"


def meta(f):
    return {"file_path": f}


class TestPerFileIds:
    def test_a_file_s_ids_do_not_depend_on_the_other_files_in_the_batch(self):
        alone = chunk_ids(C, ["a1", "a2"], [meta("a.md"), meta("a.md")])
        with_others = chunk_ids(C, ["z", "a1", "y", "a2"], [meta("z.md"), meta("a.md"), meta("y.md"), meta("a.md")])
        assert with_others[1] == alone[0] and with_others[3] == alone[1]

    def test_a_refresh_of_one_file_mints_the_ids_a_full_index_minted(self):
        full = chunk_ids(C, ["x", "b1", "b2"], [meta("x.md"), meta("b.md"), meta("b.md")])
        refresh = chunk_ids(C, ["b1", "b2"], [meta("b.md"), meta("b.md")])
        assert refresh == full[1:]

    def test_identical_text_in_two_files_never_shares_an_id(self):
        # The collision the old batch-position ids allowed: same text at the same index in two refresh runs.
        a = chunk_ids(C, ["## License"], [meta("a.md")])
        b = chunk_ids(C, ["## License"], [meta("b.md")])
        assert a != b

    def test_identical_text_twice_in_one_file_gets_two_ids(self):
        ids = chunk_ids(C, ["same", "same"], [meta("a.md"), meta("a.md")])
        assert ids[0] != ids[1]

    def test_the_collection_is_part_of_the_id(self):
        assert chunk_ids("one", ["t"], [meta("a.md")]) != chunk_ids("two", ["t"], [meta("a.md")])

    def test_chunks_with_no_file_still_get_stable_distinct_ids(self):
        ids = chunk_ids(C, ["r1", "r2"], [{}, {}])                          # release notes carry no file
        assert len(set(ids)) == 2 and ids == chunk_ids(C, ["r1", "r2"], [{}, {}])

    def test_source_url_is_the_file_when_there_is_no_file_path(self):
        assert chunk_ids(C, ["t"], [{"source_url": "https://x/a"}]) == chunk_ids(C, ["t"], [meta("https://x/a")])


def _store_with(fake):
    store = MultiCollectionStore.__new__(MultiCollectionStore)
    store._store = fake
    return store


class TestTheInsertRecorder:
    def test_inserts_are_recorded_with_their_ids_and_files_only_while_open(self):
        fake = MagicMock()
        fake.insert_data.return_value = 2
        store = _store_with(fake)
        with record_inserts() as rows:
            store.insert(C, ["a", "b"], [meta("a.md"), meta("b.md")])
        store.insert(C, ["c"], [meta("c.md")])                              # after: not recorded
        ids = chunk_ids(C, ["a", "b"], [meta("a.md"), meta("b.md")])
        assert rows == [(C, ids[0], "a", "a.md"), (C, ids[1], "b", "b.md")]
        assert fake.insert_data.call_args_list[0].kwargs["ids"] == ids

    def test_only_the_named_collections_are_recorded(self):
        # The architect's R0-1: a long refresh must not buffer every concurrent insert from other repositories.
        fake = MagicMock()
        fake.insert_data.return_value = 1
        store = _store_with(fake)
        with record_inserts({C}) as rows:
            store.insert("other_repo_code", ["x"], [meta("x.py")])
            store.insert(C, ["a"], [meta("a.md")])
        assert [r[0] for r in rows] == [C]

    def test_legacy_decimal_ids_are_still_queried(self):
        # 17 of laz's 25 voted ids are decimal, from the Python-hash era; digits are hex digits.
        fake = MagicMock()
        fake.query_by_filter.return_value = []
        _store_with(fake).rows_by_ids(C, ["468220413029671663"])
        assert fake.query_by_filter.call_count == 1

    def test_rows_by_ids_queries_only_hex_ids(self):
        fake = MagicMock()
        fake.query_by_filter.return_value = [{"id": "abc", "text": "t", "metadata": {}}]
        rows = _store_with(fake).rows_by_ids(C, ["abc", 'x" OR 1=1'])
        assert rows == [{"id": "abc", "text": "t", "metadata": {}}]
        assert fake.query_by_filter.call_count == 1


class FakeMetrics:
    def __init__(self, votes):
        self.votes = dict(votes)

    def chunk_vote_refs(self, collection):
        return [r for r in self.votes if r.startswith(collection + ":")]

    def move_chunk_votes(self, old, new):
        if old not in self.votes:
            return False
        self.votes[new] = self.votes.get(new, 0) + self.votes.pop(old)
        return True


def voted(chunk_id, text, file):
    return chunk_votes.Voted(ref=f"{C}:{chunk_id}", collection=C, chunk_id=chunk_id, text=text, file=file)


def snap(*v, already=()):
    return chunk_votes.Snapshot(collection=C, voted=list(v), already_orphaned=list(already))


class TestAlreadyOrphaned:
    """The architect's R0-2: a voted ref whose row is already gone (2 of laz's 25 votes today), or whose id cannot be
    one, is counted and reported, not silently left out."""

    def test_a_voted_ref_with_no_row_is_listed(self):
        m = FakeMetrics({f"{C}:a1": 1, f"{C}:gone": 1, f"{C}:not-hex!": 1})
        store = MagicMock()
        store.rows_by_ids.return_value = [{"id": "a1", "text": "t", "metadata": meta("a.md")}]
        got = chunk_votes.snapshot(store, C, None, metrics=m)
        assert got.already_orphaned == [f"{C}:gone", f"{C}:not-hex!"]

    def test_the_report_says_how_many(self):
        r = chunk_votes.carry(snap(already=[f"{C}:gone"]), [], metrics=FakeMetrics({}))
        assert r.already_orphaned == [f"{C}:gone"]
        assert "1 already orphaned before this re-insert" in r.words()

    def test_a_snapshot_with_only_orphans_is_still_reported(self):
        assert bool(snap(already=[f"{C}:gone"])) is True


class TestCarry:
    def test_the_same_id_coming_back_is_kept(self):
        m = FakeMetrics({f"{C}:a1": 1})
        r = chunk_votes.carry(snap(voted("a1", "t", "a.md")), [(C, "a1", "t", "a.md")], metrics=m)
        assert r.kept == [f"{C}:a1"] and m.votes == {f"{C}:a1": 1}

    def test_a_moved_chunk_takes_its_vote_to_the_new_id(self):
        m = FakeMetrics({f"{C}:old": 3})
        r = chunk_votes.carry(snap(voted("old", "t", "a.md")), [(C, "new", "t", "a.md"), (C, "z", "other", "a.md")],
                              metrics=m)
        assert r.moved == {f"{C}:old": f"{C}:new"} and m.votes == {f"{C}:new": 3}

    def test_ties_are_narrowed_to_the_same_file(self):
        m = FakeMetrics({f"{C}:old": 1})
        r = chunk_votes.carry(snap(voted("old", "## License", "a.md")),
                              [(C, "n1", "## License", "a.md"), (C, "n2", "## License", "b.md")], metrics=m)
        assert r.moved == {f"{C}:old": f"{C}:n1"}

    def test_still_tied_is_ambiguous_and_left_where_it_is(self):
        m = FakeMetrics({f"{C}:old": 1})
        r = chunk_votes.carry(snap(voted("old", "same", "a.md")),
                              [(C, "n1", "same", "a.md"), (C, "n2", "same", "a.md")], metrics=m)
        assert r.ambiguous == {f"{C}:old": 2} and m.votes == {f"{C}:old": 1}

    def test_text_that_is_gone_is_orphaned_and_kept(self):
        m = FakeMetrics({f"{C}:old": 1})
        r = chunk_votes.carry(snap(voted("old", "gone", "a.md")), [(C, "n1", "new text", "a.md")], metrics=m)
        assert r.orphaned == [f"{C}:old"] and m.votes == {f"{C}:old": 1}
        assert "1 orphaned" in r.words()

    def test_another_collection_s_inserts_are_not_candidates(self):
        m = FakeMetrics({f"{C}:old": 1})
        r = chunk_votes.carry(snap(voted("old", "t", "a.md")), [("elsewhere", "n1", "t", "a.md")], metrics=m)
        assert r.orphaned == [f"{C}:old"]

    def test_a_failing_carry_is_reported_not_raised(self):
        class Broken(FakeMetrics):
            def move_chunk_votes(self, old, new):
                raise RuntimeError("metrics down")
        r = chunk_votes.carry(snap(voted("old", "t", "a.md")), [(C, "new", "t", "a.md")], metrics=Broken({}))
        assert "metrics down" in r.error and "not carried" in r.words()


class TestSnapshot:
    def test_reads_only_voted_chunks_and_filters_to_the_files_being_replaced(self):
        m = FakeMetrics({f"{C}:a1": 1, f"{C}:b1": 1, "other:z": 1})
        store = MagicMock()
        store.rows_by_ids.return_value = [{"id": "a1", "text": "ta", "metadata": meta("a.md")},
                                          {"id": "b1", "text": "tb", "metadata": meta("b.md")}]
        got = chunk_votes.snapshot(store, C, {"a.md"}, metrics=m)
        assert sorted(store.rows_by_ids.call_args.args[1]) == ["a1", "b1"]
        assert [(v.chunk_id, v.text, v.file) for v in got.voted] == [("a1", "ta", "a.md")]
        assert got.already_orphaned == []

    def test_no_votes_reads_nothing(self):
        store = MagicMock()
        got = chunk_votes.snapshot(store, C, None, metrics=FakeMetrics({}))
        assert not got and got.voted == []
        store.rows_by_ids.assert_not_called()

    def test_an_unreadable_snapshot_is_empty_so_the_replace_still_runs(self):
        class Broken(FakeMetrics):
            def chunk_vote_refs(self, collection):
                raise RuntimeError("metrics down")
        assert not chunk_votes.snapshot(MagicMock(), C, None, metrics=Broken({}))


class TestMetricsVoteMoves:
    """MetricsCollector on SQLite: the prefix read and the merging move."""

    @pytest.fixture
    def metrics(self, tmp_path):
        from resource_explorer.observability.metrics_collector import MetricsCollector
        return MetricsCollector(database_url=f"sqlite:///{tmp_path}/metrics.db")

    def _vote(self, m, ref, pos, total):
        with m._conn() as conn:
            conn.execute("INSERT INTO chunk_feedback (chunk_ref, positive_count, neutral_count, total_count, "
                         "last_updated) VALUES (?, ?, 0, ?, 'now')", (ref, pos, total))

    def test_the_prefix_read_treats_underscore_literally(self, metrics):
        self._vote(metrics, "repo_markdown_docs:a", 1, 1)
        self._vote(metrics, "repoXmarkdown_docs:b", 1, 1)
        assert metrics.chunk_vote_refs("repo_markdown_docs") == ["repo_markdown_docs:a"]

    def test_a_move_merges_into_existing_votes_and_removes_the_old_row(self, metrics):
        self._vote(metrics, f"{C}:old", 2, 3)
        self._vote(metrics, f"{C}:new", 1, 1)
        assert metrics.move_chunk_votes(f"{C}:old", f"{C}:new") is True
        with metrics._conn() as conn:
            rows = {r["chunk_ref"]: (r["positive_count"], r["total_count"])
                    for r in conn.execute("SELECT * FROM chunk_feedback").fetchall()}
        assert rows == {f"{C}:new": (3, 4)}
        assert metrics.move_chunk_votes(f"{C}:old", f"{C}:new") is False


class TestTheRefreshCarriesVotes:
    def test_a_partial_refresh_snapshots_the_changed_files_and_carries(self, tmp_path):
        from resource_explorer.ingestion.incremental import IncrementalIndexer
        from resource_explorer.registry import Project, ProjectRegistry

        registry = ProjectRegistry(db_path=str(tmp_path / "reg.db"))
        project = Project(slug="repo", display_name="repo", github_url="https://github.com/x/repo",
                          collections=["repo_markdown_docs"])
        registry.add(project)
        indexer = IncrementalIndexer.__new__(IncrementalIndexer)
        indexer.client = MagicMock()
        indexer.registry = registry
        repo = MagicMock()
        indexer.client.get_repo.return_value = repo
        indexer.client.get_latest_commit_sha.return_value = "new"
        indexer._get_last_sha = lambda slug: "old"
        indexer._store_last_sha = lambda slug, sha: None
        indexer._get_changed_files = lambda repo, a, b: ["README.md"]
        indexer.client.download_zipball.return_value = tmp_path
        seen = {}

        def fake_snapshot(store, collection, files):
            seen["files"] = files
            return snap(voted("x", "t", "README.md"))

        def fake_carry(s, inserted):
            seen["carried"] = s.voted
            return chunk_votes.CarryReport(collection=C)
        with patch("resource_explorer.ingestion.incremental.has_own_index", return_value=True), \
             patch("resource_explorer.ingestion.incremental.IngestionPipeline") as P, \
             patch("resource_explorer.ingestion.incremental.MultiCollectionStore"), \
             patch.object(chunk_votes, "snapshot", fake_snapshot), patch.object(chunk_votes, "carry", fake_carry):
            P.return_value._ingest_collection.return_value = 1
            P.return_value._count_repo_stats.return_value = (1, 1)
            indexer.refresh(registry.get("repo"))
        assert seen["files"] == {"README.md"} and seen["carried"][0].chunk_id == "x"
