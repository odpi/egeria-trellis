"""The registration claims are atomic on the real registry engine (Postgres).

`take_claim` / `take_server_claim` rely on `INSERT ... ON CONFLICT DO NOTHING` plus `rowcount`. SQLite
proves the logic in test_catalog_and_survey.py; this re-proves it on Postgres through the suite's
`pg_registry` fixture (a throwaway resource_explorer_test_* schema, skipped when no Postgres is reachable),
so the atomicity is checked on every run that has one.
"""
from __future__ import annotations

from resource_explorer import catalog_and_survey as cas


def test_database_claim_is_taken_once_and_again_after_release_on_postgres(pg_registry):
    assert cas.take_claim(pg_registry, "adventureworks") is True
    assert cas.take_claim(pg_registry, "adventureworks") is False
    cas.release_claim(pg_registry, "adventureworks")
    assert cas.take_claim(pg_registry, "adventureworks") is True


def test_server_claim_is_taken_once_and_again_after_release_on_postgres(pg_registry):
    server = "host.docker.internal:5432"
    assert cas.take_server_claim(pg_registry, server) is True
    assert cas.take_server_claim(pg_registry, server) is False
    cas.release_server_claim(pg_registry, server)
    assert cas.take_server_claim(pg_registry, server) is True
