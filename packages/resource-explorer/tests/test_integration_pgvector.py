"""Real (non-mocked) integration test tier — migration plan Phase 8.

Neither Resource Explorer nor Egeria Advisor had any real test coverage of
their data-store code before this. Every test here is marked
`requires_pgvector` (see conftest.py) — auto-skipped when Postgres isn't
reachable, and always operating against a dedicated `resource_explorer_test`
schema, never the real `resource_explorer` schema that holds production data
for the 7 real migrated projects.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.requires_pgvector


class TestVectorStoreIntegration:
    """Real round-trips against PgVectorStore — no mocks."""

    def test_insert_search_returns_right_neighbor(self, pg_store):
        col = "it_vs_search"
        pg_store.create_collection(col, drop_if_exists=True)
        pg_store.insert_data(
            col,
            texts=[
                "The Gizmo widget uses a torque converter for smooth power delivery.",
                "Quarterly revenue grew 12% driven by the enterprise segment.",
                "To reset your password, click the link in the confirmation email.",
            ],
            ids=["a", "b", "c"],
            metadata=[{"file_path": "a.md"}, {"file_path": "b.md"}, {"file_path": "c.md"}],
        )
        results = pg_store.search(col, query_text="torque converter widget power", top_k=3)
        assert results
        assert results[0].id == "a"
        assert "torque converter" in results[0].text

    def test_delete_by_metadata_removes_exactly_targeted_rows(self, pg_store):
        col = "it_vs_delete"
        pg_store.create_collection(col, drop_if_exists=True)
        pg_store.insert_data(
            col,
            texts=["chunk one", "chunk two", "chunk three"],
            ids=["1", "2", "3"],
            metadata=[{"file_path": "x.py"}, {"file_path": "y.py"}, {"file_path": "x.py"}],
        )
        deleted = pg_store.delete_by_metadata(col, "file_path", ["x.py"])
        assert deleted == 2
        stats = pg_store.get_collection_stats(col)
        assert stats["num_entities"] == 1
        remaining = pg_store.query_by_filter(col, limit=10)
        assert [r["id"] for r in remaining] == ["2"]

    def test_collection_exists_and_drop_collection(self, pg_store):
        col = "it_vs_lifecycle"
        assert pg_store.collection_exists(col) is False
        pg_store.create_collection(col)
        assert pg_store.collection_exists(col) is True
        pg_store.delete_collection(col)
        assert pg_store.collection_exists(col) is False

    def test_rename_collection_preserves_rows(self, pg_store):
        """The repo-rename repair operation's core guarantee: renaming is an
        ALTER TABLE, not a drop+reingest, so existing rows survive under the
        new name."""
        old, new = "it_vs_rename_old", "it_vs_rename_new"
        pg_store.delete_collection(old)
        pg_store.delete_collection(new)
        pg_store.create_collection(old)
        pg_store.insert_data(old, texts=["a chunk that must survive the rename"],
                             ids=["r1"], metadata=[{"file_path": "keep.md"}])

        pg_store.rename_collection(old, new)

        assert pg_store.collection_exists(old) is False
        assert pg_store.collection_exists(new) is True
        stats = pg_store.get_collection_stats(new)
        assert stats["num_entities"] == 1

    def test_rename_collection_onto_existing_name_raises(self, pg_store):
        old, new = "it_vs_rename_collide_old", "it_vs_rename_collide_new"
        pg_store.delete_collection(old)
        pg_store.delete_collection(new)
        pg_store.create_collection(old)
        pg_store.create_collection(new)
        with pytest.raises(Exception):
            pg_store.rename_collection(old, new)

    def test_multi_collection_store_wrapper_real_round_trip(self, pg_test_schema, monkeypatch):
        """The MultiCollectionStore compatibility wrapper every real call site
        uses (not just the lower-level PgVectorStore) — insert/search/count/
        list_source_files/collection_exists/drop_collection, scoped to the
        throwaway test schema via the module-level shared-store override."""
        import resource_explorer.vector_store_pg as vsp
        from resource_explorer.vector_store_pg import MultiCollectionStore, PgVectorStore

        monkeypatch.setattr(vsp, "_shared_store", PgVectorStore(schema=pg_test_schema))
        store = MultiCollectionStore()
        col = store.collection_name("itproj", "markdown_docs")

        n = store.insert(col, ["Real pgvector round trip via MultiCollectionStore."], [{"file_path": "README.md"}])
        assert n == 1
        assert store.collection_exists(col) is True
        assert store.count(col) == 1

        results = store.search("pgvector round trip", [col], top_k=3, min_score=-1.0)
        assert results and "MultiCollectionStore" in results[0].text

        assert store.list_source_files([col]) == ["README.md"]

        renamed = store.collection_name("itproj_renamed", "markdown_docs")
        store.rename_collection(col, renamed)
        assert store.collection_exists(col) is False
        assert store.collection_exists(renamed) is True
        assert store.count(renamed) == 1

        store.drop_collection(renamed)
        assert store.collection_exists(renamed) is False


class TestRegistryIntegration:
    """Real CRUD against a Postgres-backed ProjectRegistry — single-PK and
    composite-PK tables, including an ON CONFLICT path."""

    def test_single_pk_crud(self, pg_registry):
        from resource_explorer.registry import Project, ProjectStatus

        pg_registry.add(Project(
            slug="it-single-pk", display_name="IT Single PK",
            github_url="https://github.com/a/it-single-pk",
        ))
        got = pg_registry.get("it-single-pk")
        assert got is not None and got.display_name == "IT Single PK"

        pg_registry.update_status("it-single-pk", ProjectStatus.INDEXING)
        assert pg_registry.get("it-single-pk").status.value == "indexing"

        pg_registry.remove("it-single-pk")
        assert pg_registry.get("it-single-pk") is None

    def test_composite_pk_upsert_on_conflict_path(self, pg_registry):
        """resource_schedules PK is (entity_type, entity_slug, analysis_id) —
        a real composite-key ON CONFLICT DO UPDATE round trip."""
        pg_registry.save_schedule("repo", "it-proj", "security_scan", "daily", True)
        pg_registry.save_schedule("repo", "it-proj", "security_scan", "weekly", True)  # ON CONFLICT path

        rows = pg_registry.get_schedules("repo", "it-proj")
        assert len(rows) == 1
        assert rows[0]["schedule"] == "weekly"

        assert pg_registry.delete_schedule("repo", "it-proj", "security_scan") is True
        assert pg_registry.get_schedules("repo", "it-proj") == []

    def test_alias_and_group_on_conflict_paths(self, pg_registry):
        """The two paths fixed in migration plan Phase 3 — real Postgres,
        not the SQLite-only INSERT OR REPLACE they replaced."""
        from resource_explorer.registry import Project

        pg_registry.add(Project(slug="it-alias-proj", display_name="X", github_url="https://github.com/a/x"))
        pg_registry.add_alias("itp", "it-alias-proj", confirmed_by="user")
        pg_registry.add_alias("itp", "it-alias-proj", confirmed_by="admin")  # ON CONFLICT path
        assert pg_registry.resolve_alias("itp") == "it_alias_proj"
        assert len(pg_registry.list_aliases()) == 1

        pg_registry.create_group("it-group", "IT Group", "v1")
        pg_registry.create_group("it-group", "IT Group Renamed", "v2")  # ON CONFLICT path
        group = pg_registry.get_group("it-group")
        assert group.display_name == "IT Group Renamed"


class TestEndToEndIntegration:
    """Ingest a tiny fixture repo through the real IngestionPipeline (no
    GitHub network access — local_path), then query it through the real
    rag_system.py retrieval path (CollectionRouter + MultiCollectionStore),
    with only the final LLM call stubbed (a genuinely external, optional
    dependency this test shouldn't require) to prove content really flowed
    through the whole real chain."""

    def test_ingest_then_query_returns_real_retrieved_content(
        self, pg_test_schema, tmp_path, monkeypatch,
    ):
        # 1. A tiny fixture "repo" on disk
        repo_dir = tmp_path / "fixture_repo"
        (repo_dir / "src").mkdir(parents=True)
        (repo_dir / "src" / "widget.py").write_text(
            '"""Gizmo widget pricing utilities."""\n\n'
            "def calculate_widget_price(base_cost, tax_rate):\n"
            '    """Compute the final price of a Gizmo widget including tax."""\n'
            "    return base_cost * (1 + tax_rate)\n"
        )

        # 2. Point the vector store singleton and every ProjectRegistry
        # construction site on this code path at the throwaway test schema —
        # never the real resource_explorer schema.
        import resource_explorer.vector_store_pg as vsp
        import resource_explorer.ingestion.pipeline as pipeline_mod
        import resource_explorer.rag_system as rag_mod
        import resource_explorer.collection_router as router_mod
        from resource_explorer.config import get_config
        from resource_explorer.registry import ProjectRegistry
        from resource_explorer.vector_store_pg import PgVectorStore

        monkeypatch.setattr(vsp, "_shared_store", PgVectorStore(schema=pg_test_schema))

        # RAGSystem._init_observability opens a MetricsCollector, whose
        # METRICS_DATABASE_URL defaults to the SHARED Postgres (its own
        # setting, separate from the registry's). Until 2026-10-05 this test
        # wrote 'fixtureproj' rows into the shared query_log on every run.
        # conftest now isolates the setting too; this is the explicit,
        # test-local statement of it.
        import resource_explorer.config as config_mod
        monkeypatch.setenv("METRICS_DATABASE_URL", f"sqlite:///{tmp_path}/metrics.db")
        config_mod._config = None

        cfg = get_config().pgvector
        test_url = (
            f"postgresql://{cfg.db_user}:{cfg.password}@{cfg.host}:{cfg.port}/{cfg.dbname}"
            f"?options=-csearch_path%3D{pg_test_schema}"
        )
        monkeypatch.setattr(pipeline_mod, "ProjectRegistry", lambda: ProjectRegistry(database_url=test_url))
        monkeypatch.setattr(rag_mod, "ProjectRegistry", lambda: ProjectRegistry(database_url=test_url))
        monkeypatch.setattr(router_mod, "ProjectRegistry", lambda: ProjectRegistry(database_url=test_url))

        # 3. Real ingestion — repo=None is fine, local_path skips every
        # GitHub-touching code path (download, releases, get_repo() is only
        # used for stats/release_notes, both skipped when local_path is set
        # for a non-release collection type).
        from resource_explorer.configdata.collection_config import COLLECTION_TYPES
        from resource_explorer.ingestion.pipeline import IngestionPipeline

        registry = ProjectRegistry(database_url=test_url)
        from resource_explorer.registry import Project
        registry.add(Project(
            slug="fixtureproj", display_name="Fixture Project",
            github_url="https://github.com/test/fixtureproj",
        ))

        pipeline = IngestionPipeline()
        pipeline.registry = registry
        count = pipeline._ingest_collection(
            repo=None, resource_slug="fixtureproj", collection_name="fixtureproj_python_code",
            ctype=COLLECTION_TYPES["python_code"], local_root=repo_dir,
        )
        assert count > 0
        registry.update_indexed_at("fixtureproj", ["fixtureproj_python_code"])

        # 4. Stub only the LLM — echo the prompt back so we can assert the
        # real retrieved chunk text made it all the way through.
        class _StubLLM:
            def complete(self, prompt: str, system: str = "", **kwargs) -> str:
                return f"STUB_RESPONSE::{prompt}"

        monkeypatch.setattr(rag_mod, "get_llm", lambda: _StubLLM())

        # Force GENERAL intent deterministically — this test is about the
        # real pgvector retrieval path, not routing.yaml's classification.
        from resource_explorer.query_processor import QueryIntent, QueryProcessor
        monkeypatch.setattr(QueryProcessor, "classify", lambda self, query: QueryIntent.GENERAL)

        rag = rag_mod.RAGSystem()
        response = rag.query("How does the widget calculate its price with tax?", resource_slug="fixtureproj")

        assert "STUB_RESPONSE::" in response
        assert "calculate_widget_price" in response  # real retrieved chunk content
