"""ENRICHMENT-E1-CONTEXT-TAB (2026-09-29 amendment): the prerequisite
resolver's new human-input precondition kind, and the catalog changes that
feed it.

The project owner corrected the designer's reply (§0.2, "no analysis runs at
the Enrichment stage"): a class of analysis DOES run there, when its
prerequisite is a human input rather than a survey read. These tests cover:

  - `step_preconditions.human_input_state()` for each of the four
    `requires_input` kinds the catalog now declares, including the amendment's
    own gate scenario (one declared documentation source unlocks
    doc_source_ingestion; no lens leaves preliminary_fit locked with the
    right message).
  - the catalog changes: `preliminary_fit`'s intent/requires_input retag and
    the three new stub entries, all still declared as REGISTERED to the gap
    guard.
  - `GET /api/context/database/{slug}/enrichment-analyses`, the map route.
"""
from __future__ import annotations

import types

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import DatabaseEntity, ProjectRegistry
from resource_explorer.surveyors import analysis_catalog_reader as acr
from resource_explorer.surveyors import step_preconditions


def setup_function(_fn):
    acr.clear_cache()


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.register_database(DatabaseEntity(
        slug="adventureworks", display_name="AdventureWorks", db_type="postgresql",
        host="localhost", port=5432, database_name="adventureworks",
    ))
    return r


class TestHumanInputState:
    def test_lens_not_declared_by_default(self, registry):
        project = types.SimpleNamespace(slug="adventureworks")
        present, reason = step_preconditions.human_input_state(registry, project, "lens")
        assert present is False
        assert "declare a lens" in reason

    def test_documentation_source_locked_when_none_declared(self, registry):
        """Zero declared sources reads as NOT PRESENT with the plain reason."""
        project = types.SimpleNamespace(slug="adventureworks")
        present, reason = step_preconditions.human_input_state(registry, project, "documentation_source")
        assert present is False
        assert reason == "no documentation source declared yet"

    @pytest.mark.parametrize("entity_type", ["database", "filesystem", "repo"])
    def test_documentation_source_unlocked_by_the_row_context_reads(self, registry, entity_type):
        """One declared source, written through the SAME registry method the
        Context tab's block writes and reads (`add_doc_source`), unlocks --
        by slug alone, whatever entity_type the row carries. The reader used
        to query a `documentation_sources.database_slug` table nothing ever
        created, so a source Context showed never unlocked the analysis."""
        registry.add_doc_source(entity_type, "adventureworks", "https://example.org/docs")
        project = types.SimpleNamespace(slug="adventureworks")
        present, reason = step_preconditions.human_input_state(registry, project, "documentation_source")
        assert present is True
        assert "1 documentation source" in reason

    def test_unlock_and_context_agree(self, registry):
        """The two readers over the same rows: what list_doc_sources (Context)
        counts is what the precondition counts."""
        project = types.SimpleNamespace(slug="adventureworks")
        for n in (0, 1, 2):
            while len(registry.list_doc_sources("database", "adventureworks")) < n:
                registry.add_doc_source("database", "adventureworks", f"https://example.org/{n}")
            declared = len(registry.list_doc_sources("database", "adventureworks"))
            present, _ = step_preconditions.human_input_state(registry, project, "documentation_source")
            assert present is (declared > 0)

    def test_ingested_documentation_needs_ingest_specifically(self, registry):
        """A declared-but-not-yet-ingested source must not unlock
        doc_evidence_check -- a DIFFERENT human input (ingested content)."""
        row = registry.add_doc_source("database", "adventureworks", "https://example.org/docs")
        project = types.SimpleNamespace(slug="adventureworks")
        source_present, _ = step_preconditions.human_input_state(registry, project, "documentation_source")
        ingested_present, ingested_reason = step_preconditions.human_input_state(
            registry, project, "ingested_documentation")
        assert source_present is True
        assert ingested_present is False
        assert "no documentation has been ingested" in ingested_reason
        with registry._conn() as conn:
            conn.execute("UPDATE doc_sources SET ingested_at=? WHERE id=?",
                         ("2026-09-30T00:00:00+00:00", row["id"]))
        ingested_present, _ = step_preconditions.human_input_state(
            registry, project, "ingested_documentation")
        assert ingested_present is True

    def test_confirmed_glossary_term_locked_when_nothing_confirmed(self, registry):
        project = types.SimpleNamespace(slug="adventureworks")
        present, reason = step_preconditions.human_input_state(registry, project, "confirmed_glossary_term")
        assert present is False
        assert "no glossary term confirmed" in reason

    def test_unrecognized_kind_is_conservative_not_permissive(self, registry):
        project = types.SimpleNamespace(slug="adventureworks")
        present, reason = step_preconditions.human_input_state(registry, project, "something_new")
        assert present is False
        assert "unrecognized" in reason


class TestCatalogAmendment:
    def test_preliminary_fit_is_now_enrichment_with_requires_input(self):
        entries = {a["id"]: a for a in acr.get_analyses("database", include_egeria_live=False)}
        assert entries["preliminary_fit"]["intent"] == "enrichment"
        assert entries["preliminary_fit"]["requires_input"] == "lens"

    def test_the_three_stub_entries_are_registered(self):
        entries = {a["id"]: a for a in acr.get_analyses("database", intent="enrichment",
                                                          include_egeria_live=False)}
        for aid, kind in [("doc_source_ingestion", "documentation_source"),
                          ("doc_evidence_check", "ingested_documentation"),
                          ("semantic_suggestions", "confirmed_glossary_term")]:
            assert aid in entries, f"{aid} is not registered — the map has nothing to point at"
            assert entries[aid]["requires_input"] == kind


class TestEnrichmentAnalysesRoute:
    @pytest.fixture
    def client(self, registry, monkeypatch):
        monkeypatch.setattr(
            "resource_explorer.registry.ProjectRegistry.__init__",
            lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
        )
        monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
        from resource_explorer.web.app import app
        return TestClient(app)

    def test_gate_scenario_source_unlocks_ingestion_no_lens_locks_fit(self, registry, client):
        """The amendment's exact gate (task brief): with one reachable
        documentation source declared, doc_source_ingestion reads UNLOCKED
        with a Run action; with no lens declared, preliminary_fit reads
        LOCKED with 'declare a lens on the investigation'."""
        registry.add_doc_source("database", "adventureworks", "https://example.org/docs")

        resp = client.get("/api/context/database/adventureworks/enrichment-analyses")
        assert resp.status_code == 200
        rows = {r["id"]: r for r in resp.json()["analyses"]}

        assert rows["doc_source_ingestion"]["unlocked"] is True
        assert rows["doc_source_ingestion"]["state"] == "unlocked"

        assert rows["preliminary_fit"]["unlocked"] is False
        assert rows["preliminary_fit"]["state"] == "locked"
        assert "declare a lens on the investigation" in rows["preliminary_fit"]["reason"]

    def test_repo_has_no_enrichment_entries(self, registry, client):
        resp = client.get("/api/context/repo/some-repo/enrichment-analyses")
        assert resp.status_code == 200
        assert resp.json()["analyses"] == []


class TestPreconditionQueriesHitRealSchema:
    """The original bug: the documentation_source precondition queried a
    table (`documentation_sources`) that no code ever created, and
    `_row_count` turns ANY query error into -1 -> "not present", so the
    phantom table read as a plain "none declared". These run against the
    registry's real, migrated schema (no hand-built tables) and require the
    query to SUCCEED (>= 0), so the next phantom table/column fails here."""

    def test_doc_sources_count_query_succeeds_on_the_real_schema(self, registry):
        n = step_preconditions._row_count(registry, "doc_sources", "adventureworks",
                                          slug_column="entity_slug")
        assert n == 0, "the doc_sources count query failed against the real schema"

    def test_ingested_query_succeeds_on_the_real_schema(self, registry):
        n = step_preconditions._row_count(
            registry, "doc_sources", "adventureworks",
            where="ingested_at IS NOT NULL AND ingested_at != ''", slug_column="entity_slug")
        assert n == 0, "the ingested count query failed against the real schema"


class TestRunControlsMatchRunners:
    """A Run control must never be offered for an analysis the run route
    would reject (doc_source_ingestion had a button and no runner: ingestion
    slice 2 was never built). Computed FROM the run maps and the catalog, not
    a hand-kept list, so this fails for ANY analysis added the same way."""

    @pytest.fixture
    def client(self, registry, monkeypatch):
        monkeypatch.setattr(
            "resource_explorer.registry.ProjectRegistry.__init__",
            lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
        )
        monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
        from resource_explorer.web.app import app
        return TestClient(app)

    def test_every_offered_run_control_has_a_runner_and_every_other_is_rejected(self, registry, client):
        from resource_explorer.surveyors.database.database_surveyor import DATABASE_SURVEYOR_STEP_MAP
        from resource_explorer.surveyors.database.db_derived import DB_DERIVED_ANALYSES

        # every enrichment-tier analysis the map lists, unlocked or not
        registry.add_doc_source("database", "adventureworks", "https://example.org/docs")
        rows = client.get("/api/context/database/adventureworks/enrichment-analyses").json()["analyses"]
        assert rows, "the catalog declares enrichment-tier analyses for databases"

        mapped = set(DB_DERIVED_ANALYSES) | set(DATABASE_SURVEYOR_STEP_MAP)
        for row in rows:
            # 1. the route's claim equals the run maps' own contents
            assert row["runnable"] is (row["id"] in mapped), row["id"]
            # 2. the run route agrees with the claim: a runnable analysis is
            #    not rejected for lack of a runner; an unrunnable one is.
            resp = client.post(f"/api/databases/adventureworks/analyses/{row['id']}/run")
            no_runner = resp.status_code == 400 and "no local survey step" in resp.text
            assert no_runner is (not row["runnable"]), (row["id"], resp.status_code, resp.text)

    def test_doc_source_ingestion_specifically_has_no_runner_yet(self, registry, client):
        rows = {r["id"]: r for r in client.get(
            "/api/context/database/adventureworks/enrichment-analyses").json()["analyses"]}
        assert rows["doc_source_ingestion"]["runnable"] is False
