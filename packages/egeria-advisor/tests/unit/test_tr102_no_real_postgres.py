"""TR-102: a unit test that opens a real Postgres connection fails, even when the code under test swallows the error.

The guard is pg_guard.py, installed for every unit test by conftest's autouse `_no_real_postgres`. These tests reach for
Postgres on purpose, check the refusal, then clear the record so the teardown check does not fail them.
"""
from __future__ import annotations

import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from pg_guard import PG_PORTS, RealPostgresRefused, guard_applies, install_pg_guard  # noqa: E402


def test_psycopg2_connect_is_refused_and_recorded(request):
    import psycopg2

    with pytest.raises(RealPostgresRefused, match="real Postgres connection"):
        psycopg2.connect(host="localhost", port=5442, dbname="egeria_advisor")
    assert request.node.pg_guard_hits == ["psycopg2.connect(host='localhost', port=5442)"]
    request.node.pg_guard_hits.clear()


@pytest.mark.parametrize("port", sorted(PG_PORTS))
def test_a_socket_to_a_postgres_port_is_refused_before_it_connects(request, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(RealPostgresRefused):
            s.connect(("127.0.0.1", port))
    finally:
        s.close()
    assert request.node.pg_guard_hits == [f"socket connect to 127.0.0.1:{port}"]
    request.node.pg_guard_hits.clear()


def test_a_swallowed_attempt_is_still_recorded():
    # The shape that hid the four hits: the code under test catches the failure and logs a warning.
    with pytest.MonkeyPatch.context() as mp:
        hits = install_pg_guard(mp, "inner")
        try:
            import psycopg2
            psycopg2.connect(host="localhost", port=5442)
        except Exception:  # noqa: BLE001 -- swallowed, as get_symbol_store's callers do
            pass
    assert hits and "port=5442" in hits[0]


def _request(markers=(), fixtures=()):
    node = SimpleNamespace(get_closest_marker=lambda name: name if name in markers else None)
    return SimpleNamespace(node=node, fixturenames=list(fixtures))


def test_every_unit_test_is_guarded_but_the_opt_in_postgres_tiers():
    assert guard_applies(_request())
    assert not guard_applies(_request(markers=("requires_pgvector",)))
    assert not guard_applies(_request(fixtures=("ea_test_schema",)))
