"""R1a of BRIEF-REINDEX-FROM-SCRATCH.md: swapping collections, tables AND vector indexes, in one transaction.

Against real Postgres in the throwaway test schema (`requires_pgvector`), because what is under test is Postgres's own
rename and index behaviour: one transaction for every rename (the architect's C3), indexes renamed with their tables so
the next rebuild gets one (C4), names over 63 bytes refused before anything runs (C4).
"""
from __future__ import annotations

import threading
import time

import pytest

pytestmark = pytest.mark.requires_pgvector


def _make(store, name, *, rows=("a chunk",), index=True):
    store.delete_collection(name)
    store.create_collection(name)
    store.insert_data(name, texts=list(rows), ids=[f"{name[:8]}{i}" for i in range(len(rows))],
                      metadata=[{"file_path": "f.md"} for _ in rows])
    if index:
        store.create_index(name)
    else:
        # create_collection's DDL may make the index itself; this table must have none.
        conn = store._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(f'DROP INDEX IF EXISTS {store._qualified(store.vector_index_name(name))}')
            conn.commit()
        finally:
            store._put_conn(conn)
        assert not store.has_vector_index(name)


def _indexes(store, name):
    conn = store._get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT indexname FROM pg_indexes WHERE schemaname = %s AND tablename = %s",
                        (store._catalog_schema, store._table(name)))
            return [r[0] for r in cur.fetchall()]
    finally:
        store._put_conn(conn)


def _clean(store, *names):
    for n in names:
        store.delete_collection(n)


class TestSwap:
    LIVE, REB, PREV = "sw_repo_markdown_docs", "sw_repo_markdown_docs__rebuild", "sw_repo_markdown_docs__previous"

    def test_a_swap_moves_tables_and_their_indexes_together(self, pg_store):
        _make(pg_store, self.LIVE, rows=("old one", "old two"))
        _make(pg_store, self.REB, rows=("new one", "new two", "new three"))
        _clean(pg_store, self.PREV)
        pg_store.swap_collections([(self.LIVE, self.PREV), (self.REB, self.LIVE)])
        assert pg_store.get_collection_stats(self.LIVE)["num_entities"] == 3
        assert pg_store.get_collection_stats(self.PREV)["num_entities"] == 2
        assert not pg_store.collection_exists(self.REB)
        # Each table owns the index under its own name, so the next rebuild's create_index makes a real one.
        assert pg_store.has_vector_index(self.LIVE) and pg_store.has_vector_index(self.PREV)
        # R1a-1: every index named after its table moves with it, the primary key included, so names never drift.
        assert set(_indexes(pg_store, self.LIVE)) == {f"{self.LIVE}_embedding_idx", f"{self.LIVE}_pkey"}
        assert set(_indexes(pg_store, self.PREV)) == {f"{self.PREV}_embedding_idx", f"{self.PREV}_pkey"}
        _make(pg_store, self.REB, rows=("again",))
        assert set(_indexes(pg_store, self.REB)) == {f"{self.REB}_embedding_idx", f"{self.REB}_pkey"}
        assert pg_store.has_vector_index(self.REB)
        _clean(pg_store, self.LIVE, self.PREV, self.REB)

    def test_undo_is_the_reverse_swap(self, pg_store):
        _make(pg_store, self.LIVE, rows=("old",))
        _make(pg_store, self.REB, rows=("new", "new2"))
        _clean(pg_store, self.PREV)
        pg_store.swap_collections([(self.LIVE, self.PREV), (self.REB, self.LIVE)])
        pg_store.swap_collections([(self.LIVE, self.REB), (self.PREV, self.LIVE)])
        assert pg_store.get_collection_stats(self.LIVE)["num_entities"] == 1
        assert pg_store.has_vector_index(self.LIVE) and pg_store.has_vector_index(self.REB)
        _clean(pg_store, self.LIVE, self.PREV, self.REB)

    def test_a_table_without_an_index_is_renamed_alone(self, pg_store):
        _make(pg_store, self.LIVE, index=False)
        _make(pg_store, self.REB)
        _clean(pg_store, self.PREV)
        pg_store.swap_collections([(self.LIVE, self.PREV), (self.REB, self.LIVE)])
        assert not pg_store.has_vector_index(self.PREV) and pg_store.has_vector_index(self.LIVE)
        _clean(pg_store, self.LIVE, self.PREV)

    def test_an_impossible_sequence_is_refused_before_anything_moves(self, pg_store):
        _make(pg_store, self.LIVE)
        _make(pg_store, self.PREV)                         # the previous already exists: the swap cannot run
        _make(pg_store, self.REB)
        with pytest.raises(ValueError, match="already exists"):
            pg_store.swap_collections([(self.LIVE, self.PREV), (self.REB, self.LIVE)])
        assert all(pg_store.collection_exists(n) for n in (self.LIVE, self.PREV, self.REB))
        with pytest.raises(ValueError, match="does not exist"):
            pg_store.swap_collections([("sw_nothing_here", self.LIVE)])
        _clean(pg_store, self.LIVE, self.PREV, self.REB)

    def test_a_failure_part_way_rolls_back_every_rename(self, pg_store, monkeypatch):
        _make(pg_store, self.LIVE, rows=("old",))
        _make(pg_store, self.REB, rows=("new", "new2"))
        _clean(pg_store, self.PREV)
        # Make the second ALTER fail inside the transaction: the first rename must not survive it.
        real_qualified = pg_store._qualified

        def failing(table):
            if table == pg_store._table(self.REB):
                return '"no_such_schema_for_test"."x"'
            return real_qualified(table)
        monkeypatch.setattr(pg_store, "_qualified", failing)
        import psycopg2
        with pytest.raises(psycopg2.Error):
            pg_store.swap_collections([(self.LIVE, self.PREV), (self.REB, self.LIVE)], attempts=1)
        monkeypatch.undo()
        assert pg_store.get_collection_stats(self.LIVE)["num_entities"] == 1
        assert not pg_store.collection_exists(self.PREV)
        assert pg_store.has_vector_index(self.LIVE)
        _clean(pg_store, self.LIVE, self.REB)

    def test_a_reader_never_sees_the_live_table_missing(self, pg_store):
        """C3: the swap is one transaction. A reader polling the live table's existence throughout never finds it
        absent."""
        _make(pg_store, self.LIVE, rows=("old",))
        _make(pg_store, self.REB, rows=("new",))
        _clean(pg_store, self.PREV)
        missing, stop = [], threading.Event()

        def poll():
            while not stop.is_set():
                if not pg_store.collection_exists(self.LIVE):
                    missing.append(time.monotonic())
        t = threading.Thread(target=poll)
        t.start()
        try:
            for _ in range(5):
                pg_store.swap_collections([(self.LIVE, self.PREV), (self.REB, self.LIVE)])
                pg_store.swap_collections([(self.LIVE, self.REB), (self.PREV, self.LIVE)])
        finally:
            stop.set()
            t.join()
        assert missing == []
        _clean(pg_store, self.LIVE, self.REB, self.PREV)


class TestNames:
    def test_a_name_postgres_would_truncate_is_refused_before_anything_runs(self, pg_store):
        long = "r" * 50 + "_markdown_docs"                  # 64 bytes as a table; the index name is longer still
        assert pg_store.check_names(long)
        with pytest.raises(ValueError, match="truncates"):
            pg_store.swap_collections([(long, long + "__previous")])

    def test_names_that_fit_have_no_problems(self, pg_store):
        assert pg_store.check_names("repo_markdown_docs", "repo_markdown_docs__rebuild",
                                    "repo_markdown_docs__previous") == []

    def test_the_index_name_counts_too(self, pg_store):
        # A table name that fits can still have an index name that does not.
        name = "t" * 50
        assert len(name.encode()) <= 63 and pg_store.check_names(name)


class TestLockTimeout:
    LIVE, REB, PREV = "lk_repo_docs", "lk_repo_docs__rebuild", "lk_repo_docs__previous"

    def _hold_lock(self, store, table, seconds):
        """Another connection reads the table in an open transaction for `seconds`, holding a lock a RENAME waits on."""
        conn = store._pool.getconn() if hasattr(store, "_pool") else store._get_conn()
        ready = threading.Event()

        def run():
            with conn.cursor() as cur:
                cur.execute(f"SELECT count(*) FROM {store._qualified(store._table(table))}")
                ready.set()
                time.sleep(seconds)
            conn.rollback()
            store._put_conn(conn)
        t = threading.Thread(target=run)
        t.start()
        ready.wait(5)
        return t

    def test_a_held_lock_times_out_and_the_swap_retries_until_it_is_free(self, pg_store):
        _make(pg_store, self.LIVE)
        _make(pg_store, self.REB)
        _clean(pg_store, self.PREV)
        holder = self._hold_lock(pg_store, self.LIVE, 1.5)
        started = time.monotonic()
        pg_store.swap_collections([(self.LIVE, self.PREV), (self.REB, self.LIVE)],
                                  lock_timeout="300ms", attempts=5, retry_wait_s=0.5)
        holder.join()
        assert time.monotonic() - started >= 0.3                 # it did wait, and then it got through
        assert pg_store.collection_exists(self.PREV) and pg_store.collection_exists(self.LIVE)
        _clean(pg_store, self.LIVE, self.PREV)

    def test_a_lock_held_past_every_attempt_raises_and_moves_nothing(self, pg_store):
        _make(pg_store, self.LIVE)
        _make(pg_store, self.REB)
        _clean(pg_store, self.PREV)
        holder = self._hold_lock(pg_store, self.LIVE, 3)
        import psycopg2
        with pytest.raises(psycopg2.errors.LockNotAvailable):
            pg_store.swap_collections([(self.LIVE, self.PREV), (self.REB, self.LIVE)],
                                      lock_timeout="200ms", attempts=2, retry_wait_s=0.2)
        holder.join()
        assert pg_store.collection_exists(self.REB) and not pg_store.collection_exists(self.PREV)
        _clean(pg_store, self.LIVE, self.REB)


def test_table_bytes_is_measured_and_zero_for_a_missing_table(pg_store):
    _make(pg_store, "tb_repo_docs", rows=("one", "two"))
    assert pg_store.table_bytes("tb_repo_docs") > 0
    assert pg_store.table_bytes("tb_nothing_here") == 0
    _clean(pg_store, "tb_repo_docs")
