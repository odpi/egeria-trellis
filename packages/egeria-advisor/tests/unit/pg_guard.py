"""TR-102: EA's unit tests never open a real Postgres.

A socket/psycopg2 guard (Architect, TR-98 read-back 2026-11-20) caught four real connections in the unit suite, all
from test_ingest_collections: scripts/ingest_collections.py clears a collection's code symbols through
get_symbol_store() -> CodeSymbolStore -> a real psycopg2 pool on EA's configured database (the shared PG on laz; likely
what Rocky hit on 5442). The code under test catches the failure and logs a warning, so a refusal alone would pass
silently: conftest's autouse `_no_real_postgres` records every attempt and FAILS the test at teardown, naming it.

Opt-in tiers keep their access: a test marked requires_pgvector, or using `ea_test_schema` (a throwaway schema of its
own), is gated by tests/conftest.py (a supplied password and a reachable server) and is not guarded.
"""
from __future__ import annotations

#: Postgres ports a unit test must never reach: the standard one and the shared PG's.
PG_PORTS = frozenset({5432, 5442})


class RealPostgresRefused(RuntimeError):
    """A unit test tried to open a real Postgres connection (TR-102)."""


def guard_applies(request) -> bool:
    """Every unit test is guarded except the opt-in Postgres tiers."""
    return not (request.node.get_closest_marker("requires_pgvector") or "ea_test_schema" in request.fixturenames)


def install_pg_guard(monkeypatch, where: str) -> list:
    """Refuse psycopg2.connect and socket connects to PG_PORTS; return the list each attempt is recorded in."""
    import socket

    hits: list = []

    def _refuse(what: str):
        hits.append(what)
        raise RealPostgresRefused(f"{where}: a unit test opened a real Postgres connection ({what}); stub it")

    try:
        import psycopg2
    except ImportError:  # pragma: no cover - psycopg2 is an EA dependency
        psycopg2 = None
    if psycopg2 is not None:
        def _connect(*args, **kwargs):
            _refuse(f"psycopg2.connect(host={kwargs.get('host')!r}, port={kwargs.get('port')!r})")
        monkeypatch.setattr(psycopg2, "connect", _connect)

    real_connect = socket.socket.connect

    def _socket_connect(self, address):
        port = address[1] if isinstance(address, tuple) and len(address) > 1 else None
        if port in PG_PORTS:
            _refuse(f"socket connect to {address[0]}:{port}")
        return real_connect(self, address)
    monkeypatch.setattr(socket.socket, "connect", _socket_connect)
    return hits
