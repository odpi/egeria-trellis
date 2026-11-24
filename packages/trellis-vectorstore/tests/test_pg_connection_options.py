"""TR-60a: PgVectorStoreConfig.options reaches EVERY connection the store opens; None sends none (RE unchanged).

EA's tables are unqualified (schema=None). Through the role's search_path, "$user", public, the role egeria_advisor
read the EMPTY copies in the schema egeria_advisor (TR-60). EA now sends "-c search_path=public" as a connection
option, which overrides the role's default; this pins that the store carries it to the pool, the bootstrap
connection and the declared-collections read.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from trellis_vectorstore.config import PgVectorStoreConfig
from trellis_vectorstore.pg import PgVectorStore

OPTS = "-c search_path=public"


class _FakeEmbeddings:
    def embed_texts(self, texts):
        return [[0.0] * 384 for _ in texts]

    def embed_query(self, text):
        return [0.0] * 384


def _store(options=None, **kw):
    config = PgVectorStoreConfig(host="localhost", port=5442, dbname="test", user="u", password="p", options=options)
    return PgVectorStore(config, metric="cosine", embeddings=_FakeEmbeddings(), **kw)


def _connect(store):
    pool_kwargs = {}

    def _pool(*a, **k):
        pool_kwargs.update(k)
        return MagicMock()

    with patch("trellis_vectorstore.pg.ThreadedConnectionPool", side_effect=_pool), \
         patch("trellis_vectorstore.pg.psycopg2.connect") as mock_connect:
        mock_connect.return_value.__enter__.return_value = MagicMock()
        mock_connect.return_value.__exit__.return_value = False
        store.connect()
    return pool_kwargs, mock_connect.call_args.kwargs


def test_options_reach_the_pool_and_the_bootstrap_connection():
    pool_kwargs, bootstrap_kwargs = _connect(_store(options=OPTS))
    assert pool_kwargs["options"] == OPTS
    assert bootstrap_kwargs["options"] == OPTS


def test_no_options_sends_none_so_re_is_unchanged():
    pool_kwargs, bootstrap_kwargs = _connect(_store())
    assert "options" not in pool_kwargs and "options" not in bootstrap_kwargs


def test_the_declared_collections_read_carries_them_too():
    store = _store(options=OPTS, ddl="external", declared_collections_table="declared")
    with patch("trellis_vectorstore.pg.psycopg2.connect") as mock_connect:
        conn = MagicMock()
        mock_connect.return_value.__enter__.return_value = conn
        mock_connect.return_value.__exit__.return_value = False
        conn.cursor.return_value.fetchone.return_value = (1,)
        store._require_declared("pyegeria")
    assert mock_connect.call_args.kwargs["options"] == OPTS
