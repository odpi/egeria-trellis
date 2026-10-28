"""No row may reference a resource slug that no longer exists.

This is a trap turned into a test. Two of them, really:

  * `project_published_annotation_types` and `project_published_analyses` carry
    a `project_slug` with NO declared foreign key to `projects.slug`, so
    Postgres will not stop a delete from stranding their rows -- and
    `ProjectRegistry.remove()` does not clean them up.
  * Removing the stale `trellis` repo on 2026-08-28 stranded exactly one row in
    `project_published_annotation_types`. Nothing noticed. Nothing would have.

The FK-declared tables are already safe by construction; the value here is the
ones that are not, and the fact that this test DISCOVERS slug-bearing tables
from the live schema rather than from a hand-maintained list. A list would have
been written against today's schema and gone stale the first time a table was
added -- which is exactly how the two above came to exist unguarded.
"""
from __future__ import annotations

import pytest

from resource_explorer.registry import Project, ProjectRegistry

pytestmark = pytest.mark.requires_pgvector


@pytest.fixture
def pg_registry(pg_test_schema):
    """A registry on the throwaway integration schema, never the live
    `resource_explorer` one -- this test deletes things."""
    from resource_explorer.config import get_config

    cfg = get_config().pgvector
    url = (f"postgresql://{cfg.db_user}:{cfg.password}@{cfg.host}:{cfg.port}"
           f"/{cfg.dbname}?options=-csearch_path%3D{pg_test_schema}")
    return ProjectRegistry(database_url=url)

# Tables whose slug column deliberately outlives the resource. Empty today:
# `project_published_analyses` is *suspected* to be audit history, but that is
# inferred from a test comment rather than stated anywhere authoritative, so it
# is not exempted on a guess. If it should be, add it here WITH the reason.
# Slug-bearing tables with NO declared foreign key to projects.slug. Postgres
# will not stop a delete from stranding their rows, so each needs a reason.
#
# Both were found by audit on 2026-08-28, not by design. Removing the stale
# `trellis` repo the same day stranded a row in the first, and nothing noticed.
UNGUARDED_BY_DESIGN: dict[str, str] = {
    "project_published_annotation_types":
        "published to Egeria; rows outlive the local repo. NOTE: not cleaned "
        "up by remove() either, which is a separate open question.",
    "project_published_analyses":
        "suspected audit history -- inferred from a test comment, not stated "
        "anywhere authoritative. Confirm or clean up.",
    # Found by THIS TEST on first run, not by the hand audit that found the two
    # above. Investigated and resolved 2026-08-28 -- each keeps its slug rows on
    # purpose, and for a different reason.
    "conversation_history":
        "chat turns, keyed on session_id; project_slug is optional CONTEXT "
        "(DEFAULT '') and not identity -- a conversation need not have a repo "
        "at all. The conversation happened; deleting the repo does not unhappen "
        "it. Same class as query_log. 14 rows, 0 orphaned.",
    "repo_dispositions":
        "keyed on github_url, NOT on slug -- project_slug is secondary "
        "annotation. The primary case is a decision about a repo that was NEVER "
        "registered: 'ignored' means passed on during scouting, and the row is "
        "what stops it resurfacing as new in Discovery. A foreign key would "
        "FORBID that case, so this table must be unguarded to work at all. "
        "17 rows, 1 already orphaned -- which is correct behaviour, not a leak.",
    "sub_resources":
        "12 of 13 rows carry an egeria_guid: these are records of things "
        "PUBLISHED to Egeria, and the Egeria element outlives the local repo. "
        "Same class as project_published_annotation_types. Narrower follow-up "
        "worth having: a sub-resource with NO egeria_guid is pure orphan debris "
        "on removal, and remove() cleans neither.",
    "resource_scope_events":
        "append-only audit log of what a person decided to publish or leave out "
        "(who, when, which locator). The rows are the record of that decision and "
        "may outlive the resource; nothing updates or deletes them, ever.",
    "query_log":
        "observability history -- what was asked about a resource is analytics "
        "that outlives the resource, and feedback tuning reads it. Deliberate.",
    "context_compiles":
        "every compile the apps ran, keyed on a content-addressed compile_id; "
        "project_slug is the resource the compile was ABOUT, not the row's "
        "identity. conversation_history and feedback rows point at compile_id, "
        "and the compiled-vs-RAG experiment reads it back, so the row must "
        "outlive the resource for the same reason query_log does: the compile "
        "happened, and the feedback that cites it still needs its manifest. "
        "Added 2026-09-08 (design §13); rename is handled via _PROJECT_SLUG_TABLES.",
}

# This allowlist is a RATCHET, in the manner of
# tests/no_silent_success_baseline.json: it records where things stand so a new
# unguarded table cannot appear unnoticed, and it may only shrink. Every entry
# now carries a real reason; none is a placeholder. Adding a new entry to keep
# the build green is the one use this list is not for.
#
# The reasons fall into two kinds, and the distinction is the useful part:
#   - the row describes something that HAPPENED (a question asked, a decision
#     made). Deleting the resource does not un-happen it.
#   - the row points at something that still EXISTS elsewhere, in Egeria.
#     Deleting the local resource does not delete the remote element.
# A row that is neither -- one whose only purpose is to describe a resource
# that is now gone -- is orphan debris and should cascade instead.


def _connect():
    import psycopg2

    from resource_explorer.config import get_config

    cfg = get_config().pgvector
    conn = psycopg2.connect(
        host=cfg.host, port=cfg.port, dbname=cfg.dbname,
        user=cfg.db_user, password=cfg.password,
    )
    conn.autocommit = True
    return conn


def _slug_bearing_tables(cur, schema: str) -> list[tuple[str, str]]:
    """(table, column) for every column that holds a resource slug."""
    cur.execute(
        """SELECT table_name, column_name
             FROM information_schema.columns
            WHERE table_schema = %s
              AND column_name IN ('project_slug', 'resource_slug')
         ORDER BY table_name, column_name""",
        (schema,),
    )
    return [(r[0], r[1]) for r in cur.fetchall()]


class TestNoOrphanedSlugs:
    def test_discovery_finds_slug_columns(self, pg_registry, pg_test_schema):
        """Guards the discovery itself. If this stops finding slug columns, the
        check below silently passes by inspecting nothing -- the failure mode
        this file exists to prevent, one level up. pg_registry is requested
        first so the schema is provisioned before it is read."""
        conn = _connect()
        try:
            with conn.cursor() as cur:
                found = _slug_bearing_tables(cur, pg_test_schema)
        finally:
            conn.close()
        assert len(found) > 5, f"only {len(found)} slug column(s) -- discovery is broken"

    def test_every_unguarded_slug_table_is_accounted_for(self, pg_registry, pg_test_schema):
        """The real check, and it cannot be vacuous.

        A table carrying a resource slug with no FK to projects.slug can strand
        rows on delete, silently. That is not always wrong -- rows published to
        Egeria may legitimately outlive the local resource -- but it must be a
        decision, not an accident. A new one appearing here means someone added
        a slug column without deciding.
        """
        conn = _connect()
        try:
            with conn.cursor() as cur:
                slug_tables = {t for t, _ in _slug_bearing_tables(cur, pg_test_schema)}
                cur.execute(
                    """SELECT DISTINCT tc.table_name
                         FROM information_schema.table_constraints tc
                         JOIN information_schema.key_column_usage kcu
                           ON kcu.constraint_name = tc.constraint_name
                          AND kcu.table_schema = tc.table_schema
                        WHERE tc.table_schema = %s
                          AND tc.constraint_type = 'FOREIGN KEY'
                          AND kcu.column_name IN ('project_slug', 'resource_slug')""",
                    (pg_test_schema,),
                )
                guarded = {r[0] for r in cur.fetchall()}
        finally:
            conn.close()

        unguarded = slug_tables - guarded
        undeclared = sorted(unguarded - set(UNGUARDED_BY_DESIGN))
        assert not undeclared, (
            "slug-bearing table(s) with no foreign key and no recorded reason: "
            f"{undeclared}. Either add the FK, or add an entry to "
            "UNGUARDED_BY_DESIGN saying why rows may outlive the resource."
        )

    def test_the_allowlist_does_not_rot(self):
        """An entry for a table that no longer exists is a stale exemption
        hiding behind real ones.

        Checked against the tables DECLARED IN CODE, not against a database.
        That is the complete, deterministic universe -- 54 of them, spanning
        every component that owns one.

        Two earlier versions got that universe wrong in opposite directions,
        and both failed in CI rather than locally:

        * Against the LIVE `resource_explorer` schema, on the argument that it
          "is the only complete set". True on a developer machine. In CI the
          workflow creates that schema EMPTY (`CREATE SCHEMA IF NOT EXISTS`)
          and tables appear only as tests happen to create them; the guard
          skipped an empty schema but not a partial one, so CI saw a handful,
          concluded five existing tables had been dropped, and failed every
          run. That schema is also too broad -- ~101 tables, including
          per-project pgvector collections this allowlist has no opinion on.
        * Against the throwaway schema after `pg_registry` provisions it.
          Complete for the tables `ProjectRegistry` owns, and only those:
          `query_log` is created by `observability/metrics_collector.py`, so
          it read as dropped.

        The source is the only place that knows about all of them regardless
        of which component provisions it, and it cannot be partially
        populated. This test now needs no database and cannot fail for
        environmental reasons -- which matters because it is a ratchet, and a
        permanently red ratchet teaches people to merge past it until a real
        staleness looks identical to the noise.
        """
        import re
        from pathlib import Path

        import resource_explorer

        pattern = re.compile(
            r'CREATE TABLE (?:IF NOT EXISTS )?"?([a-z_]+)"?', re.IGNORECASE)
        root = Path(resource_explorer.__file__).parent
        declared: set[str] = set()
        for source in root.rglob("*.py"):
            declared |= set(pattern.findall(source.read_text(errors="ignore")))

        assert len(declared) > 40, (
            f"only {len(declared)} CREATE TABLE statement(s) found -- the scan "
            "is broken, and this check would then pass by inspecting nothing"
        )

        gone = sorted(set(UNGUARDED_BY_DESIGN) - declared)
        assert not gone, (
            f"UNGUARDED_BY_DESIGN lists table(s) that are gone: {gone}"
        )
