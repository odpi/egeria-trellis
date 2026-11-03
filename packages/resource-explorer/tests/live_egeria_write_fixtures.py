"""Live-write harness for the `live_egeria_writes` pytest tier.

    ==========================================================================
    COORDINATION NOTICE — READ BEFORE USING ANY FIXTURE IN THIS MODULE
    ==========================================================================
    Every fixture here performs REAL writes (catalogue, then delete) against
    the shared dev Egeria platform that other Claude Code sessions on this
    machine also use for real work. Per this project's `coordinate-shared-
    writes` discipline (see the skill of that name, and
    `PLAN-EXECUTION-MODES-VERIFICATION.md` phase 4), running
    any test that pulls in `live_egeria_write_target` (or any other fixture
    below) is NOT something you get to do unilaterally, even though the
    elements created are thrown away within the same test:

      1. Before the FIRST run in a session, call ListAgents, message every
         live peer describing exactly what will be catalogued and deleted
         and why concurrent writes are unsafe (or why they're safe — dedup by
         a session-unique qualified-name prefix is the argument here, see
         below), and wait for explicit clearance from everyone who can write.
      2. A "looks idle" read of ListAgents is not consent. Silence is not
         consent.
      3. Run the write exactly once per coordination round. This tier is not
         meant to be looped for local iteration — if a test using it fails,
         fix it by reasoning about the failure, not by re-running until it
         passes (this project's shared-write discipline again: verify
         independently, never re-run to check).

    This is why `live_egeria_writes` is a SEPARATE, more restrictive marker
    than `requires_egeria` (see conftest.py's `pytest_collection_modifyitems`
    and the design note PHASE-4-LIVE-HARNESS-IMPLEMENTED.md for the reasoning
    in full). `requires_egeria` only certifies that a platform happens to be
    reachable — a read-only-safe, always-on-when-possible check. Writing to
    dev Egeria, even throwaway writes, is a bigger commitment than "Egeria
    happens to be up" and needs a *deliberate* second gate: the
    `--live-egeria-writes` CLI flag, mirroring the existing `--corpus` /
    `skip_corpus` precedent in conftest.py. A plain `pytest tests/` run must
    never exercise this tier just because Egeria happens to be reachable at
    that moment — that would turn an ordinary developer laptop run into an
    unannounced shared write.

Collision safety
-----------------
Every element this module catalogues carries a qualified-name prefix of
``test-fixture-phase4-<uuid4 hex>-`` — unique per fixture invocation, not just
per test session — so two sessions running this tier at the same moment
(against advice above, or after both got clearance for genuinely disjoint
work) cannot collide on a shared name the way `AutomatedCuration.
create_*_element_from_template` would if two callers raced on the same
`postgres_server`/`postgres_database`/`folder_name` value. It does not make
concurrent runs *safe* by itself — the coordination step above is still
mandatory — it only removes "the two runs picked the same name" as a
possible failure mode, in the same spirit as the note in
`reference_egeria_dedup_and_link_patterns.md` about qualifiedName collisions.
"""
from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

log = logging.getLogger(__name__)

#: Same sentinel pyegeria's AutomatedCuration.get_guid_for_name (and this
#: repo's own _find_element_guid wrappers) use for "no such element" — a
#: string, not an exception, so treating it as "not found" here matches how
#: the rest of the codebase reads this call.
_NOT_FOUND_SENTINEL = "No elements found"

#: Bound every pyegeria call this module makes. Per
#: project_re_server_stuck_incident_2026_09_04.md, pyegeria's
#: get_guid_for_name has a real, still-unfixed cross-thread/event-loop hang
#: risk; run_sync's timeout contains it (the worker is abandoned, not
#: joined) rather than letting a stuck call hang this fixture's teardown
#: forever and leak the underlying elements with no diagnosis at all.
_CALL_TIMEOUT_SECONDS = 30.0


def _new_suffix() -> str:
    """A per-invocation, collision-proof identifier — see module docstring."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True)
class LiveEgeriaWriteTarget:
    """What phases 5-6 get from `live_egeria_write_target`.

    `database_guid`/`database_qualified_name` and `filesystem_guid`/
    `filesystem_qualified_name` identify the throwaway elements this fixture
    catalogued for the duration of the test. `server_guid` is exposed too,
    since it is a real, separately-deletable element the database element
    depends on (see `_catalog_throwaway_database`) and a Path B2/C live test
    may need it (e.g. to trigger a server-level survey).
    """

    suffix: str
    server_guid: str
    server_qualified_name: str
    database_guid: str
    database_qualified_name: str
    filesystem_guid: str
    filesystem_qualified_name: str
    #: RE_LIVE_EGERIA_WRITES_CLEARED at the time of the write: who cleared it
    #: and the peer round it names. Copied into every live write's test report.
    cleared_by: str = ""


def _connect():
    """One bearer-token-authenticated AutomatedCuration + AssetMaker pair,
    built the same way `EgeriaDatabaseSurveyor.connect()` and
    `tests/test_egeria_live_smoke.py`'s `asset_maker` fixture do, so this
    harness exercises the same client construction path production code and
    the existing live-read tier already use rather than inventing a third.
    """
    from pyegeria import AssetMaker, AutomatedCuration

    from resource_explorer.config import get_config

    cfg = get_config().egeria
    automated_curation = AutomatedCuration(
        cfg.view_server, cfg.platform_url, cfg.user_id, cfg.user_password
    )
    automated_curation.create_egeria_bearer_token(cfg.user_id, cfg.user_password)

    asset_maker = AssetMaker(cfg.view_server, cfg.platform_url, cfg.user_id, cfg.user_password)
    asset_maker.create_egeria_bearer_token(cfg.user_id, cfg.user_password)

    return automated_curation, asset_maker


def _guid_exists(automated_curation, name: str) -> bool:
    """True if Egeria still resolves `name` to an element.

    Used only for teardown verification — "the delete call didn't raise" is
    not evidence of anything on its own (this project's own convention, see
    feedback_checks_weaker_than_they_look.md), so this queries independently
    rather than trusting delete_asset's silence.
    """
    from resource_explorer.concurrency import run_sync

    try:
        result = run_sync(
            automated_curation.get_guid_for_name, name, timeout=_CALL_TIMEOUT_SECONDS
        )
    except Exception as exc:  # noqa: BLE001 - absence-check must not raise
        log.warning(
            "post-teardown existence check for %r raised rather than answering "
            "found/not-found (%s) — treating as 'could not verify', which the "
            "caller must not read as 'confirmed gone'",
            name, exc,
        )
        raise
    if isinstance(result, str):
        return bool(result) and result != _NOT_FOUND_SENTINEL and "No elements" not in result
    if isinstance(result, list):
        return len(result) > 0
    return bool(result)


def _delete_and_verify_gone(asset_maker, automated_curation, guid: str, name: str, label: str) -> list[str]:
    """Delete one element, then independently confirm it is gone.

    Returns a list of problem strings (empty on full success) rather than
    raising immediately, so a test using multiple throwaway elements reports
    every teardown failure at once instead of stopping at the first.
    """
    from resource_explorer.concurrency import run_sync

    problems: list[str] = []
    try:
        run_sync(asset_maker.delete_asset, guid, timeout=_CALL_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 - record and keep tearing down the rest
        problems.append(f"{label} delete_asset({guid!r}) raised: {exc}")

    try:
        if _guid_exists(automated_curation, name):
            problems.append(
                f"{label} {name!r} (guid {guid!r}) still resolves by name after "
                f"delete_asset — teardown did NOT actually remove it from Egeria."
            )
    except Exception as exc:  # noqa: BLE001
        problems.append(
            f"{label} {name!r} (guid {guid!r}): could not verify absence after "
            f"delete ({exc}) — treat this as UNVERIFIED, not as confirmed clean."
        )
    return problems


@pytest.fixture
def live_egeria_write_target(request):
    """Catalogue one throwaway database + one throwaway filesystem element in
    dev Egeria, yield their identifiers, then delete and verify both are gone.

    Function-scoped, not session-scoped: phase 5/6 tests using this will each
    trigger real (async, Egeria-side) survey activity against whatever this
    fixture catalogues, and a shared session-scoped target would mean one
    test's Egeria-side survey state leaking into the next test's assertions.
    The cataloguing itself (~2 template-based create calls) is cheap enough
    that per-test scope does not meaningfully slow the suite, and it keeps
    each test's blast radius to itself — a test that discovers a bug and
    fails mid-way still tears down only what it made.

    Gated on the `live_egeria_writes` marker (see conftest.py); this fixture
    body does NOT re-check the CLI flag or reachability itself, matching how
    `pg_test_schema` only checks `_PGVECTOR_AVAILABLE` for the direct-use
    case, not for the marker-driven skip which happens before the test body
    (and therefore this fixture) ever runs. It DOES still guard directly
    against a missing config below, since a test could in principle request
    this fixture without collection ever having applied the marker skip
    (e.g. if invoked via `-k` in a way that bypasses `pytest_collection_
    modifyitems` — defense in depth, not the primary gate).
    """
    from resource_explorer.config import get_config
    from resource_explorer.concurrency import run_sync

    cfg = get_config().egeria
    if not cfg.platform_url:
        pytest.skip("EGERIA_PLATFORM_URL is not configured")

    # Defense in depth, like the platform_url check: the marker skip is the
    # primary gate, but a fixture reached some other way must not write
    # without the clearance that names the peer round.
    from tests.live_egeria_tier import CLEARED_ENV, WRITES_CLEARANCE_REASON
    cleared_by = os.environ.get(CLEARED_ENV, "").strip()
    if not cleared_by:
        pytest.skip(WRITES_CLEARANCE_REASON)
    # Into the junit/report properties and onto the terminal report.
    request.node.user_properties.append(("live_egeria_writes_cleared_by", cleared_by))
    print(f"live Egeria write: cleared by {cleared_by}")

    automated_curation, asset_maker = _connect()

    suffix = _new_suffix()
    prefix = f"test-fixture-phase4-{suffix}"

    server_name = f"{prefix}-pg-server"
    database_name = f"{prefix}-pg-database"
    folder_path = f"/tmp/{prefix}-fs-root"
    folder_name = f"{prefix}-fs-root"

    server_guid = ""
    database_guid = ""
    filesystem_guid = ""
    created: list[tuple[str, str, str]] = []  # (label, guid, name), in creation order

    try:
        # ── Throwaway database: server element, then database element ──────
        # Deliberately fake host/port — this harness never needs a real
        # reachable Postgres, because nothing here triggers a native survey
        # (that would need real connection details and is phase 5/6's job,
        # not this one's). create_*_element_from_template only records
        # metadata; it does not dial the host.
        server_guid = run_sync(
            automated_curation.create_postgres_server_element_from_template,
            postgres_server=server_name,
            host_name="test-fixture-phase4.invalid",
            port="54329",
            db_user="test-fixture",
            db_pwd="test-fixture",
            description=(
                f"THROWAWAY — phase 4 live-write harness fixture, suffix={suffix}. "
                "Safe to delete if found orphaned; see "
                "packages/resource-explorer/tests/live_egeria_write_fixtures.py."
            ),
            timeout=_CALL_TIMEOUT_SECONDS,
        )
        created.append(("postgres server", server_guid, server_name))

        database_guid = run_sync(
            automated_curation.create_postgres_database_element_from_template,
            postgres_database=database_name,
            server_name=server_name,
            host_identifier="test-fixture-phase4.invalid",
            port="54329",
            db_user="test-fixture",
            db_pwd="test-fixture",
            description=(
                f"THROWAWAY — phase 4 live-write harness fixture, suffix={suffix}. "
                "Safe to delete if found orphaned."
            ),
            timeout=_CALL_TIMEOUT_SECONDS,
        )
        created.append(("postgres database", database_guid, database_name))

        # ── Throwaway filesystem: one DataFolder element ────────────────────
        filesystem_guid = run_sync(
            automated_curation.create_folder_element_from_template,
            path_name=folder_path,
            folder_name=folder_name,
            file_system=f"{prefix}-fs",
            description=(
                f"THROWAWAY — phase 4 live-write harness fixture, suffix={suffix}. "
                "Safe to delete if found orphaned."
            ),
            timeout=_CALL_TIMEOUT_SECONDS,
        )
        created.append(("filesystem folder", filesystem_guid, folder_name))

        yield LiveEgeriaWriteTarget(
            suffix=suffix,
            server_guid=server_guid,
            server_qualified_name=server_name,
            database_guid=database_guid,
            database_qualified_name=database_name,
            filesystem_guid=filesystem_guid,
            filesystem_qualified_name=folder_name,
            cleared_by=cleared_by,
        )
    finally:
        # Teardown runs even if the test body raised, or if cataloguing above
        # only got partway through (`created` records only what actually
        # succeeded). Reverse creation order: database before server, in
        # case the database element holds a containment/nesting relationship
        # to the server that would make deleting the server first leave the
        # database dangling instead of removed.
        problems: list[str] = []
        for label, guid, name in reversed(created):
            if not guid:
                continue
            problems.extend(_delete_and_verify_gone(asset_maker, automated_curation, guid, name, label))

        if problems:
            detail = "\n  - ".join(problems)
            raise AssertionError(
                f"live_egeria_write_target teardown left dev Egeria dirty "
                f"(suffix={suffix}):\n  - {detail}\n\n"
                "These are real orphaned elements in the shared dev platform — "
                "do not assume a later run's cataloguing will collide with or "
                "clean these up (each run uses a fresh suffix). Delete them by "
                "GUID manually, or via AssetMaker.delete_asset, and note it to "
                "any live peers."
            )
