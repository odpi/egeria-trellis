"""
Tests for advisor/session_state.py — the Postgres-backed SessionState store
(docs/design/SESSION_AND_INTERACTION_STATE.md, docs/runtime-architecture-plan.md §4).

`requires_pgvector`-marked tests need a live reachable Postgres instance at
the configured PGVECTOR_HOST/PORT; auto-skipped (not errored) when one
isn't available — same pattern as resource-explorer's integration tier
(packages/resource-explorer/tests/conftest.py).

Every row this suite writes uses a `test_session_` / `test_user_` prefixed
id and is deleted in a fixture teardown, so it never pollutes the real
`advisor_sessions` table another session/demo user might be reading.
"""
from __future__ import annotations

import time
import uuid

import pytest

try:
    import psycopg2
    from advisor.config import settings

    def _pgvector_reachable() -> bool:
        try:
            conn = psycopg2.connect(
                host=settings.pgvector_host, port=settings.pgvector_port,
                dbname=settings.pgvector_dbname, user=settings.pgvector_user,
                password=settings.pgvector_password, connect_timeout=2,
            )
            conn.close()
            return True
        except Exception:
            return False

    # TR-12 R1: no supplied password (only the conftest placeholder), no connection attempt.
    _PGVECTOR_AVAILABLE = (settings.pgvector_password not in ("", "test-only-not-a-password")
                           and _pgvector_reachable())
except Exception:
    _PGVECTOR_AVAILABLE = False

#: The shared marker: tests/conftest.py skips it with "PGVECTOR_PASSWORD not set (TR-12)" or "not reachable", and
#: TRELLIS_REQUIRE_PG=1 (the gate) makes either a failure.
requires_pgvector = pytest.mark.requires_pgvector


@pytest.fixture
def sid():
    """A fresh, uniquely-prefixed session_id for this test — never collides
    with a real session and is easy to spot/clean up if a teardown is ever
    skipped by a crash."""
    return f"test_session_{uuid.uuid4().hex}"


@pytest.fixture(autouse=True)
def _cleanup(sid):
    yield
    if _PGVECTOR_AVAILABLE:
        from advisor import session_state
        session_state.expire(sid)


@requires_pgvector
def test_upsert_then_get_round_trips(sid):
    from advisor import session_state

    row = session_state.upsert(sid, "test_user_alice", {"active_draft_id": "draft_1", "mode": "draft"})
    assert row["session_id"] == sid
    assert row["user_id"] == "test_user_alice"

    fetched = session_state.get(sid)
    assert fetched is not None
    assert fetched["user_id"] == "test_user_alice"
    assert fetched["state"] == {"active_draft_id": "draft_1", "mode": "draft"}
    assert fetched["expires_at"] > time.time()


@requires_pgvector
def test_get_missing_session_returns_none():
    from advisor import session_state
    assert session_state.get("test_session_does_not_exist_" + uuid.uuid4().hex) is None


@requires_pgvector
def test_touch_updates_last_seen_and_state_without_needing_user_id(sid):
    from advisor import session_state

    session_state.upsert(sid, "test_user_bob", {"mode": "idle"})
    first = session_state.get(sid)

    time.sleep(0.01)
    touched = session_state.touch(sid, state={"mode": "report_modal"})
    assert touched is not None
    assert touched["state"] == {"mode": "report_modal"}
    assert touched["last_seen_at"] > first["last_seen_at"]
    # user_id is untouched by touch() — it only needs session_id
    assert touched["user_id"] == "test_user_bob"


@requires_pgvector
def test_touch_missing_session_returns_none():
    from advisor import session_state
    assert session_state.touch("test_session_does_not_exist_" + uuid.uuid4().hex) is None


@requires_pgvector
def test_upsert_is_idempotent_and_preserves_created_at(sid):
    from advisor import session_state

    first = session_state.upsert(sid, "test_user_carol", {"mode": "idle"})
    time.sleep(0.01)
    second = session_state.upsert(sid, "test_user_carol", {"mode": "draft"})

    stored = session_state.get(sid)
    assert stored["state"] == {"mode": "draft"}
    # created_at in the DB row is preserved across the ON CONFLICT UPDATE —
    # it still matches the *first* upsert's timestamp, not the second's,
    # even though upsert() stamps a fresh `now` locally on every call.
    assert stored["created_at"] == pytest.approx(first["created_at"], abs=0.001)
    assert stored["created_at"] != pytest.approx(second["created_at"], abs=0.001)


@requires_pgvector
def test_expire_deletes_session(sid):
    from advisor import session_state

    session_state.upsert(sid, "test_user_dave", {"mode": "idle"})
    assert session_state.get(sid) is not None
    assert session_state.expire(sid) is True
    assert session_state.get(sid) is None
    # expiring an already-gone session is a no-op, not an error
    assert session_state.expire(sid) is False


@requires_pgvector
def test_expired_session_not_returned_by_get(sid):
    from advisor import session_state

    session_state.upsert(sid, "test_user_erin", {"mode": "idle"}, expires_at=time.time() - 1)
    assert session_state.get(sid) is None


@requires_pgvector
def test_sweep_expired_removes_only_expired_rows(sid):
    from advisor import session_state

    expired_sid = sid + "_expired"
    live_sid = sid + "_live"
    try:
        session_state.upsert(expired_sid, "test_user_frank", {}, expires_at=time.time() - 10)
        session_state.upsert(live_sid, "test_user_frank", {}, expires_at=time.time() + 3600)

        session_state.sweep_expired()

        assert session_state.get(expired_sid) is None
        assert session_state.get(live_sid) is not None
    finally:
        session_state.expire(expired_sid)
        session_state.expire(live_sid)


@requires_pgvector
def test_session_expires_with_the_jwt_not_a_fixed_ttl(sid):
    """A caller passing the JWT's own exp claim gets a session that expires
    exactly then — not at some independent session TTL. This is the "session
    store is Postgres-backed... sessions expire with the JWT" requirement."""
    from advisor import session_state

    jwt_exp = time.time() + 120  # a JWT with 2 minutes left
    row = session_state.upsert(sid, "test_user_grace", {"mode": "idle"}, expires_at=jwt_exp)
    assert row["expires_at"] == jwt_exp
    stored = session_state.get(sid)
    assert stored["expires_at"] == jwt_exp


# ---------------------------------------------------------------------------
# Ownership: a session created under one user is not readable by another —
# 404-shaped None, not a distinguishable error (see get_for_owner()'s
# docstring for why: it must not let a caller enumerate other users'
# session_ids by telling "doesn't exist" apart from "exists, not yours").
# ---------------------------------------------------------------------------

@requires_pgvector
def test_owner_can_read_their_own_session(sid):
    from advisor import session_state
    session_state.upsert(sid, "test_user_owner", {"mode": "draft"})
    assert session_state.get_for_owner(sid, "test_user_owner") is not None


@requires_pgvector
def test_other_user_cannot_read_someone_elses_session(sid):
    from advisor import session_state
    session_state.upsert(sid, "test_user_owner", {"mode": "draft"})
    assert session_state.get_for_owner(sid, "test_user_stranger") is None


@requires_pgvector
def test_missing_and_unauthorized_session_are_indistinguishable(sid):
    """Both a nonexistent session_id and someone else's real one come back
    None from get_for_owner — the 404-not-403 property."""
    from advisor import session_state
    session_state.upsert(sid, "test_user_owner", {"mode": "draft"})

    missing = session_state.get_for_owner("test_session_never_existed_" + uuid.uuid4().hex, "test_user_stranger")
    unauthorized = session_state.get_for_owner(sid, "test_user_stranger")
    assert missing is None
    assert unauthorized is None


@requires_pgvector
def test_anonymous_sessions_visible_to_anonymous_requester(sid):
    """user_id=None (anonymous) sessions stay readable by another anonymous
    request — matches `_anonymous_rag_mode` keeping the shared namespace
    usable with no login."""
    from advisor import session_state
    session_state.upsert(sid, None, {"mode": "idle"})
    assert session_state.get_for_owner(sid, None) is not None
