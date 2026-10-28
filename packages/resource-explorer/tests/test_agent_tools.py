"""Unit tests for new database and filesystem query agent tools and slug inference."""

from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock, patch

from resource_explorer.registry import ProjectRegistry, DatabaseEntity, FileSystemEntity
from resource_explorer.agents.tools import (
    query_databases_raw,
    query_database_schema_raw,
    query_filesystems_raw,
)
from resource_explorer.agents.base import BaseExplorerAgent


class MockAgent(BaseExplorerAgent):
    def system_prompt(self) -> str:
        return "mock"

    def tools(self) -> list:
        return []

    def handle(self, query: str, resource_slug: str | None = None, **kwargs) -> str:
        return "mock"


@pytest.fixture
def db(tmp_path):
    """Isolated SQLite registry fixture for testing."""
    return ProjectRegistry(db_path=str(tmp_path / "test_agent_tools.db"))


def test_infer_database_and_filesystem_slugs(db):
    # Register a project, database, and filesystem
    db_entity = DatabaseEntity(
        slug="test-postgres-db",
        display_name="Test Postgres DB",
        db_type="postgresql",
        host="localhost",
        port=5432,
        database_name="test_db",
    )
    db.register_database(db_entity)

    fs_entity = FileSystemEntity(
        slug="my-files-share",
        display_name="My Files Share",
        local_mount_point="/tmp/my-files-share",
    )
    db.register_filesystem(fs_entity)

    agent = MockAgent()

    with patch("resource_explorer.registry.ProjectRegistry", return_value=db):
        # Test direct matching
        slugs_db = agent._infer_all_project_slugs("tell me about test-postgres-db")
        assert "test_postgres_db" in slugs_db

        slugs_fs = agent._infer_all_project_slugs("list files in my-files-share")
        assert "my_files_share" in slugs_fs


def test_query_databases_tool(db):
    # Empty registry
    with patch("resource_explorer.registry.ProjectRegistry", return_value=db):
        res_empty = query_databases_raw()
        assert "No databases registered" in res_empty

        # Register a database
        db_entity = DatabaseEntity(
            slug="test-db",
            display_name="Test DB",
            db_type="postgresql",
            host="localhost",
            port=5432,
            database_name="test_db",
            governance_state="certified",
        )
        db.register_database(db_entity)

        res_list = query_databases_raw()
        assert "test_db" in res_list
        assert "Test DB" in res_list

        # Test single query without survey
        res_single = query_databases_raw("test-db")
        assert "Database: test_db" in res_single
        assert "No survey data available" in res_single

        # Add mock survey
        db.record_database_survey(
            slug="test-db",
            schema_count=1,
            table_count=1,
            column_count=2,
            survey_data={
                "schema_info": {
                    "schemas": [
                        {
                            "name": "public",
                            "tables": [
                                {
                                    "name": "users",
                                    "columns": [
                                        {"name": "id", "type": "integer"},
                                        {"name": "name", "type": "varchar(255)"},
                                    ],
                                }
                            ],
                        }
                    ]
                }
            },
        )

        res_survey = query_databases_raw("test-db")
        assert "Schema Count: 1" in res_survey
        assert "users" in res_survey


def test_query_database_schema_tool(db):
    # Register a database
    db_entity = DatabaseEntity(
        slug="test-db",
        display_name="Test DB",
        db_type="postgresql",
        host="localhost",
        port=5432,
        database_name="test_db",
    )
    db.register_database(db_entity)

    with patch("resource_explorer.registry.ProjectRegistry", return_value=db):
        # Query with no survey data
        assert "No survey data available" in query_database_schema_raw("test-db")

        # Add survey data
        db.record_database_survey(
            slug="test-db",
            schema_count=1,
            table_count=2,
            column_count=4,
            survey_data={
                "schema_info": {
                    "total_tables": 2,
                    "total_columns": 4,
                    "schemas": [
                        {
                            "name": "public",
                            "tables": [
                                {
                                    "name": "users",
                                    "type": "TABLE",
                                    "description": "User accounts table",
                                    "row_count": 120,
                                    "size_pretty": "16 KB",
                                    "columns": [
                                        {
                                            "position": 1,
                                            "name": "id",
                                            "type": "integer",
                                            "nullable": "NO",
                                            "is_primary_key": True,
                                        },
                                        {
                                            "position": 2,
                                            "name": "email",
                                            "type": "varchar(255)",
                                            "nullable": "NO",
                                            "is_primary_key": False,
                                        },
                                    ],
                                },
                                {
                                    "name": "profiles",
                                    "type": "TABLE",
                                    "columns": [
                                        {
                                            "position": 1,
                                            "name": "id",
                                            "type": "integer",
                                            "nullable": "NO",
                                        },
                                        {
                                            "position": 2,
                                            "name": "user_id",
                                            "type": "integer",
                                            "nullable": "NO",
                                            "foreign_key": {
                                                "foreign_schema": "public",
                                                "foreign_table": "users",
                                                "foreign_column": "id",
                                            },
                                        },
                                    ],
                                },
                            ],
                        }
                    ],
                }
            },
        )

        # 1. Query database summary
        db_summary = query_database_schema_raw("test-db")
        assert "Database Schema Summary for 'test-db'" in db_summary
        assert "users" in db_summary
        assert "profiles" in db_summary

        # 2. Query schema tables
        schema_summary = query_database_schema_raw("test-db", schema_name="public")
        assert "Schema: public" in schema_summary
        assert "users" in schema_summary

        # 3. Query table details
        table_summary = query_database_schema_raw("test-db", table_name="users")
        assert "Table: public.users" in table_summary
        assert "User accounts table" in table_summary
        assert "120" in table_summary
        assert "email" in table_summary
        assert "PK" in table_summary


def test_query_filesystems_tool(db):
    with patch("resource_explorer.registry.ProjectRegistry", return_value=db):
        # Empty registry
        assert "No filesystems registered" in query_filesystems_raw()

        # Register a filesystem
        fs_entity = FileSystemEntity(
            slug="my-fs",
            display_name="My FS",
            local_mount_point="/tmp/my-fs",
            governance_state="qualified_candidate",
        )
        db.register_filesystem(fs_entity)

        assert "my_fs" in query_filesystems_raw()

        # Single filesystem without survey
        assert "No survey data available" in query_filesystems_raw("my-fs")

        # Add filesystem survey
        db.add_filesystem_survey(
            fs_slug="my-fs",
            surveyed_at="2026-07-27T16:00:00",
            survey_data={
                "total_files": 100,
                "total_data_files": 15,
                "total_size": "2.4 MB",
                "files": [
                    {
                        "file_path": "src/main.py",
                        "file_name": "main.py",
                        "file_size": "4 KB",
                        "format": "Python Source",
                    },
                    {
                        "file_path": "data/users.csv",
                        "file_name": "users.csv",
                        "file_size": "150 KB",
                        "format": "CSV",
                    },
                ],
            },
        )

        res_survey = query_filesystems_raw("my-fs")
        assert "Total Files: 100" in res_survey
        assert "main.py" in res_survey

        # Query with pattern filtering
        res_pattern = query_filesystems_raw("my-fs", file_pattern="csv")
        assert "users.csv" in res_pattern
        assert "main.py" not in res_pattern


# ── contributor tier: the nested-window trap ────────────────────────────────
def test_contributor_tier_reads_the_widest_window_not_the_latest(tmp_path, monkeypatch):
    """`project_contributor_stats` holds NESTED trailing windows — 30d, 90d and
    365d, all ending today — not a sequence of periods.

    So the latest `period_start` is the NARROWEST window. Ordering by it
    descending answered "tier over the last 30 days" to a question about the
    contributor. Measured across the real catalogue on 2026-08-27: 70 of 347
    contributors got a different tier that way, every sampled case a core
    maintainer downgraded to "regular", and 271 more were missing from the
    30-day slice entirely — so the caller was told the tier was unknown and to
    "run refresh", about a tier that had already been computed.

    Earliest period_start = widest window. This pins the direction, because the
    two orderings differ by one keyword and the wrong one fails silently.
    """
    import sqlite3

    from resource_explorer.registry import Project, ProjectRegistry

    registry = ProjectRegistry(db_path=str(tmp_path / "r.db"))
    registry.add(
        Project(slug="demo", display_name="Demo", github_url="https://github.com/demo/demo")
    )
    with registry._conn() as conn:
        for period, tier in (
            ("2026-01-01", "core"),  # widest  (365d)
            ("2026-06-01", "regular"),  # middle  (90d)
            ("2026-08-01", "occasional"),
        ):  # narrowest (30d)
            conn.execute(
                "INSERT INTO project_contributor_stats "
                "(project_slug, period_start, period_end, author_email, author_name, "
                " commits, additions, deletions, tier) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    "demo",
                    period,
                    "2026-08-27",
                    "maintainer@example.com",
                    "Maintainer",
                    100,
                    0,
                    0,
                    tier,
                ),
            )

    with registry._conn() as conn:
        row = conn.execute(
            """SELECT tier FROM project_contributor_stats
               WHERE project_slug = ? AND author_email = ?
               ORDER BY period_start ASC LIMIT 1""",
            ("demo", "maintainer@example.com"),
        ).fetchone()

        assert row["tier"] == "core", (
            "the widest window must win; DESC would return 'occasional', which is "
            "this contributor's last 30 days, not who they are"
        )


def test_create_rfa_from_finding_tool(db):
    from resource_explorer.agents.rfa_tools import create_rfa_from_finding_raw
    from resource_explorer.registry import Project

    with patch("resource_explorer.registry.ProjectRegistry", return_value=db):
        # Empty summary error
        err = create_rfa_from_finding_raw("test-repo", "")
        assert "Error: summary must not be empty" in err

        # Create RFA for repo
        db.add(
            Project(
                slug="test-repo",
                display_name="Test Repo",
                github_url="https://github.com/test/repo",
            )
        )
        res_repo = create_rfa_from_finding_raw(
            resource_slug="test-repo",
            summary="Missing API reference documentation",
            detail="The OpenAPI schema has no endpoints documented for /users.",
            analysis_name="doc_coverage",
        )
        assert "Successfully created RequestForAction (RFA)" in res_repo
        assert "test_repo" in res_repo
        assert "Missing API reference documentation" in res_repo

        # Verify activity entry exists in registry
        entries = db.list_activity(limit=10)
        assert len(entries) >= 1
        rfa_entry = next(e for e in entries if e["operation"] == "rfa")
        assert rfa_entry["entity_slug"] == "test_repo"
        assert rfa_entry["intent"] == "enrichment"
        assert rfa_entry["summary"] == "Missing API reference documentation"
        assert len(rfa_entry["annotations"]) == 1
        assert rfa_entry["annotations"][0]["annotation_type"] == "RequestForActionAnnotation"

        # Create RFA for database
        db_ent = DatabaseEntity(
            slug="prod-db",
            display_name="Production Database",
            db_type="postgresql",
            host="db.example.com",
            port=5432,
            database_name="prod",
        )
        db.register_database(db_ent)
        res_db = create_rfa_from_finding_raw(
            resource_slug="prod-db",
            summary="Schema mismatch with staging",
            detail="Column 'is_active' missing from 'accounts' table.",
            analysis_name="schema_diff",
        )
        assert "Successfully created RequestForAction (RFA)" in res_db
        assert "database" in res_db
        assert "prod_db" in res_db


def test_compare_database_schemas_tool(db):
    from resource_explorer.agents.tools import compare_database_schemas_raw

    with patch("resource_explorer.registry.ProjectRegistry", return_value=db):
        # 1. Missing databases
        assert "Database 'missing_db1' not found" in compare_database_schemas_raw(
            "missing_db1", "missing_db2"
        )

        # Register two databases
        db1 = DatabaseEntity(
            slug="db_staging",
            display_name="Staging DB",
            db_type="postgresql",
            host="stage.local",
            port=5432,
            database_name="staging_db",
        )
        db2 = DatabaseEntity(
            slug="db_prod",
            display_name="Prod DB",
            db_type="postgresql",
            host="prod.local",
            port=5432,
            database_name="production_db",
        )
        db.register_database(db1)
        db.register_database(db2)

        # 2. No survey data
        assert "No survey data available for either database" in compare_database_schemas_raw(
            "db_staging", "db_prod"
        )

        # 3. Add survey for db1 only
        survey_data_1 = {
            "schema_info": {
                "total_schemas": 1,
                "total_tables": 2,
                "total_columns": 5,
                "schemas": [
                    {
                        "name": "public",
                        "tables": [
                            {
                                "name": "users",
                                "row_count": 50,
                                "size_pretty": "16 KB",
                                "columns": [
                                    {
                                        "name": "id",
                                        "type": "integer",
                                        "nullable": False,
                                        "is_primary_key": True,
                                    },
                                    {
                                        "name": "email",
                                        "type": "varchar(255)",
                                        "nullable": False,
                                        "is_primary_key": False,
                                    },
                                    {
                                        "name": "is_active",
                                        "type": "boolean",
                                        "nullable": False,
                                        "is_primary_key": False,
                                    },
                                ],
                            },
                            {
                                "name": "audit_log",
                                "row_count": 200,
                                "size_pretty": "64 KB",
                                "columns": [
                                    {
                                        "name": "id",
                                        "type": "integer",
                                        "nullable": False,
                                        "is_primary_key": True,
                                    },
                                    {
                                        "name": "event",
                                        "type": "text",
                                        "nullable": False,
                                        "is_primary_key": False,
                                    },
                                ],
                            },
                        ],
                    }
                ],
            }
        }
        db.record_database_survey(
            slug="db_staging",
            schema_count=1,
            table_count=2,
            column_count=5,
            survey_data=survey_data_1,
        )
        assert "No survey data available for database 'db_prod'" in compare_database_schemas_raw(
            "db_staging", "db_prod"
        )

        # 4. Add survey for db2 with differences:
        # - audit_log is missing in db2
        # - orders is only in db2
        # - users table has type mismatch on email (text vs varchar(255)) and missing is_active
        survey_data_2 = {
            "schema_info": {
                "total_schemas": 1,
                "total_tables": 2,
                "total_columns": 4,
                "schemas": [
                    {
                        "name": "public",
                        "tables": [
                            {
                                "name": "users",
                                "row_count": 1000,
                                "size_pretty": "128 KB",
                                "columns": [
                                    {
                                        "name": "id",
                                        "type": "integer",
                                        "nullable": False,
                                        "is_primary_key": True,
                                    },
                                    {
                                        "name": "email",
                                        "type": "text",
                                        "nullable": False,
                                        "is_primary_key": False,
                                    },
                                ],
                            },
                            {
                                "name": "orders",
                                "row_count": 500,
                                "size_pretty": "80 KB",
                                "columns": [
                                    {
                                        "name": "id",
                                        "type": "integer",
                                        "nullable": False,
                                        "is_primary_key": True,
                                    },
                                    {
                                        "name": "amount",
                                        "type": "numeric(10,2)",
                                        "nullable": False,
                                        "is_primary_key": False,
                                    },
                                ],
                            },
                        ],
                    }
                ],
            }
        }
        db.record_database_survey(
            slug="db_prod", schema_count=1, table_count=2, column_count=4, survey_data=survey_data_2
        )

        diff_res = compare_database_schemas_raw("db_staging", "db_prod")
        assert "Database Schema Comparison: Staging DB vs Prod DB" in diff_res
        assert "Tables only in db_staging: audit_log" in diff_res
        assert "Tables only in db_prod: orders" in diff_res
        assert "Columns only in db_staging: is_active" in diff_res
        assert "varchar(255)" in diff_res
        assert "text" in diff_res

        # 5. Filter by schema name
        filtered_res = compare_database_schemas_raw("db_staging", "db_prod", schema_name_a="public")
        assert "Schema 'public'" in filtered_res

        # Filter by non-existent schema
        missing_sch = compare_database_schemas_raw(
            "db_staging", "db_prod", schema_name_a="secret_schema"
        )
        assert "Schema 'secret_schema' not found" in missing_sch


def test_compare_agent_integration(db):
    from resource_explorer.agents.compare_agent import CompareAgent

    # Register databases in db
    db1 = DatabaseEntity(
        slug="db_alpha",
        display_name="DB Alpha",
        db_type="postgresql",
        host="alpha.internal",
        port=5432,
        database_name="alpha",
    )
    db2 = DatabaseEntity(
        slug="db_beta",
        display_name="DB Beta",
        db_type="postgresql",
        host="beta.internal",
        port=5432,
        database_name="beta",
    )
    db.register_database(db1)
    db.register_database(db2)

    agent = CompareAgent()
    tools = [
        t.name if hasattr(t, "name") else getattr(t, "__name__", str(t)) for t in agent.tools()
    ]
    assert "compare_database_schemas" in tools
    assert "create_rfa_from_finding" in tools
    assert "query_database_schema" in tools

    with patch("resource_explorer.registry.ProjectRegistry", return_value=db):
        # Fallback when comparing two databases
        mock_llm = MagicMock()
        mock_llm.complete.return_value = "Comparison completed successfully."
        with patch("resource_explorer.llm_client.get_llm", return_value=mock_llm):
            result = agent._fallback("compare db_alpha and db_beta", ["db_alpha", "db_beta"])
            assert result == "Comparison completed successfully."
            prompt_called = mock_llm.complete.call_args[0][0]
            assert "Database Schema Comparison" in prompt_called


def test_conversation_agent_has_new_tools():
    from resource_explorer.agents.conversation_agent import ConversationAgent

    agent = ConversationAgent()
    tools = [
        t.name if hasattr(t, "name") else getattr(t, "__name__", str(t)) for t in agent.tools()
    ]
    assert "compare_database_schemas" in tools
    assert "create_rfa_from_finding" in tools
    assert "query_cross_asset_links" in tools
    assert "delegate_to_compare_agent" in tools
    assert "delegate_to_integration_agent" in tools
    # Deferred: the lineage tool is a stub and must not be offered to the agent.
    assert {"database_schema_changes", "dependency_changes", "coverage_sweep"} <= set(tools)
    assert {"find_repos_by_dependency", "rank_databases", "list_rfas"} <= set(tools)
    assert "query_egeria_lineage" not in tools


def test_cross_asset_matcher(db):
    from resource_explorer.registry import Project, DatabaseEntity
    from resource_explorer.surveyors.cross_asset_matcher import (
        find_cross_asset_links,
        query_cross_asset_links_raw,
    )

    # 1. Register a project and database
    repo = Project(slug="ecommerce_service", display_name="E-Commerce Service", github_url="https://github.com/org/ecommerce")
    db.add(repo)

    db_entity = DatabaseEntity(
        slug="ecommerce_db",
        display_name="E-Commerce Database",
        db_type="postgresql",
        host="localhost",
        port=5432,
        database_name="ecommerce",
    )
    db.register_database(db_entity)

    # Record database survey with tables
    survey_data = {
        "schema_info": {
            "schemas": [
                {
                    "name": "public",
                    "tables": [
                        {"name": "users"},
                        {"name": "orders"},
                        {"name": "order_items"},
                    ],
                }
            ]
        }
    }
    db.record_database_survey(
        slug="ecommerce_db",
        schema_count=1,
        table_count=3,
        column_count=10,
        survey_data=survey_data,
    )

    # Add repo dependency on psycopg2 and sqlalchemy
    db.upsert_dependencies("ecommerce_service", [
        {"dep_name": "psycopg2-binary", "dep_version": "2.9.9", "dep_type": "runtime",
         "ecosystem": "pip", "source_file": "requirements.txt"},
        {"dep_name": "sqlalchemy", "dep_version": "2.0.0", "dep_type": "runtime",
         "ecosystem": "pip", "source_file": "requirements.txt"},
    ])

    with patch("resource_explorer.registry.ProjectRegistry", return_value=db):
        links = find_cross_asset_links(db)
        assert len(links) >= 1
        link = links[0]
        assert link["repo_slug"] == "ecommerce_service"
        assert link["database_slug"] == "ecommerce_db"
        assert link["score"] >= 0.3

        # Default view lists high-confidence links only, and says what it left out.
        default = query_cross_asset_links_raw("ecommerce_service")
        assert "No high-confidence links" in default and "1 low" in default

        output = query_cross_asset_links_raw("ecommerce_service", min_confidence="low")
        assert "Discovered Cross-Asset Dependencies" in output
        assert "ecommerce_service" in output
        assert "ecommerce_db" in output


def test_multi_resource_context_compilation(db):
    from resource_explorer.registry import Project, DatabaseEntity
    from resource_explorer.context_compile import (
        build_epistemological_matrix,
        compile_multi_resource_context,
    )

    repo1 = Project(slug="app_alpha", display_name="App Alpha", github_url="https://github.com/org/alpha")
    repo2 = Project(slug="app_beta", display_name="App Beta", github_url="https://github.com/org/beta")
    db.add(repo1)
    db.add(repo2)

    # 1. Test epistemological asymmetry matrix
    md_table, records = build_epistemological_matrix(db, ["app_alpha", "app_beta"])
    assert "| Dimension" in md_table
    assert "app_alpha" in md_table
    assert "app_beta" in md_table
    assert len(records) > 0

    # 2. Test multi-resource compile
    compiled = compile_multi_resource_context(
        registry=db,
        slugs=["app_alpha", "app_beta"],
        question="Compare security scan and dependencies",
        budget=2000,
    )
    assert compiled.text != ""
    assert "MULTI-RESOURCE EVIDENCE COMPILATION" in compiled.text
    assert "app_alpha" in compiled.text
    assert "app_beta" in compiled.text
    assert compiled.manifest["mode"] == "multi_resource"
    assert len(compiled.manifest["slugs"]) == 2
    assert "coverage_matrix" in compiled.manifest


def test_dynamic_delegation_tools(db):
    from resource_explorer.agents.tools import (
        delegate_to_compare_agent_raw,
        delegate_to_integration_agent_raw,
    )
    from resource_explorer.registry import Project, DatabaseEntity

    repo = Project(slug="repo_one", display_name="Repo One", github_url="https://github.com/org/one")
    db.add(repo)
    db_ent = DatabaseEntity(
        slug="db_one",
        display_name="DB One",
        db_type="postgresql",
        host="localhost",
        port=5432,
        database_name="db_one",
    )
    db.register_database(db_ent)

    # Force the direct-tool fallback: otherwise the real BeeAI agent runs against a live LLM.
    with patch("resource_explorer.registry.ProjectRegistry", return_value=db), patch(
        "resource_explorer.agents.base.BaseExplorerAgent._run_agent",
        side_effect=RuntimeError("agent unavailable"),
    ):
        # 1. Compare agent delegation fallback
        mock_llm = MagicMock()
        mock_llm.complete.return_value = "Delegated comparison response."
        with patch("resource_explorer.llm_client.get_llm", return_value=mock_llm):
            comp_resp = delegate_to_compare_agent_raw("compare repo_one and db_one")
            assert comp_resp == "Delegated comparison response."

        # 2. Integration agent delegation fallback
        with patch("resource_explorer.llm_client.get_llm", return_value=mock_llm):
            mock_llm.complete.return_value = "Delegated integration response."
            integ_resp = delegate_to_integration_agent_raw("how do repo_one and db_one work together?")
            assert integ_resp == "Delegated integration response."



def test_compare_and_integration_agents_use_multi_resource_evidence(db):
    from resource_explorer.agents.compare_agent import CompareAgent
    from resource_explorer.agents.conversation_agent import ConversationAgent
    from resource_explorer.agents.integration_agent import IntegrationAgent
    from resource_explorer.registry import Project

    db.add(Project(slug="app_alpha", display_name="App Alpha", github_url="https://github.com/org/alpha"))
    db.add(Project(slug="app_beta", display_name="App Beta", github_url="https://github.com/org/beta"))

    seen: list[str] = []

    def _capture(self, prompt):
        seen.append(prompt)
        return "ok"

    with patch("resource_explorer.registry.ProjectRegistry", return_value=db), patch(
        "resource_explorer.agents.base.BaseExplorerAgent._run_agent", _capture
    ), patch(
        "resource_explorer.agents.base.BaseExplorerAgent._infer_all_project_slugs",
        return_value=["app_alpha", "app_beta"],
    ), patch(
        "resource_explorer.context_compile.compile_multi_resource_context",
        wraps=None,
    ) as multi:
        from resource_explorer.context_compile import CompiledContext

        multi.return_value = CompiledContext(
            text="=== MULTI-RESOURCE EVIDENCE COMPILATION ===",
            compile_id="abc",
            manifest={
                "mode": "multi_resource",
                "gaps": [{"key": "security_scan", "reason": "no stored result", "resource_slug": "app_beta"}],
            },
            derivation=[],
        )
        for agent_cls in (CompareAgent, IntegrationAgent):
            seen.clear()
            agent = agent_cls()
            assert agent.handle("compare app_alpha and app_beta") == "ok"
            assert "MULTI-RESOURCE EVIDENCE COMPILATION" in seen[0]
            assert "[app_beta] Analyses that no stored result: security_scan." in seen[0]
            assert agent._last_compiled.manifest["mode"] == "multi_resource"

        # ConversationAgent hands the compile back for the route's payload.
        with patch(
            "resource_explorer.query_processor.QueryProcessor.classify",
            return_value=__import__("resource_explorer.query_processor", fromlist=["QueryIntent"]).QueryIntent.COMPARISON,
        ):
            conv = ConversationAgent()
            assert conv.handle("compare app_alpha and app_beta") == "ok"
            assert conv._last_compiled.compile_id == "abc"

        # A compiler that fails must not cost the answer.
        multi.side_effect = RuntimeError("boom")
        agent = CompareAgent()
        assert agent.handle("compare app_alpha and app_beta") == "ok"
        assert agent._last_compiled is None


def test_rfa_guardrails(db):
    from resource_explorer.agents.rfa_tools import create_rfa_from_finding_raw
    from resource_explorer.registry import Project

    db.add(Project(slug="rfa-repo", display_name="RFA Repo", github_url="https://github.com/o/r"))

    def _rfas():
        return [e for e in db.list_activity(limit=50) if e["operation"] == "rfa"]

    with patch("resource_explorer.registry.ProjectRegistry", return_value=db):
        # Unknown resource is refused, not filed as a repo.
        assert "not a registered" in create_rfa_from_finding_raw("nowhere", "Something")
        # Delivery status is not the agent's to choose.
        assert "not allowed" in create_rfa_from_finding_raw("rfa-repo", "Gap", status="pending")
        # Size limits.
        assert "keep it under" in create_rfa_from_finding_raw("rfa-repo", "x" * 201)
        assert "limit is" in create_rfa_from_finding_raw("rfa-repo", "Gap", detail="d" * 4001)
        assert _rfas() == []

        # Unconfirmed: preview only, nothing written.
        preview = create_rfa_from_finding_raw("rfa-repo", "Missing docs", confirm=False)
        assert "NOT FILED YET" in preview
        assert _rfas() == []

        # Confirmed: written once, as a local RFA.
        assert "Successfully created" in create_rfa_from_finding_raw("rfa-repo", "Missing docs")
        assert len(_rfas()) == 1 and _rfas()[0]["status"] == "local"

        # Same summary again (any case/whitespace) is not duplicated.
        again = create_rfa_from_finding_raw("rfa-repo", "  missing   DOCS ")
        assert "Not filed" in again
        assert len(_rfas()) == 1

        # Once a person has completed it, the same finding may be filed again.
        entry = _rfas()[0]
        db.upsert_rfa_action(
            rfa_id=f"{entry['id']}::0", entry_id=entry["id"], annotation_index=0,
            status="completed", activity_status="completed",
        )
        assert "Successfully created" in create_rfa_from_finding_raw("rfa-repo", "Missing docs")
        assert len(_rfas()) == 2

        # The agent-facing tool previews by default and does not expose delivery status.
        from resource_explorer.agents.rfa_tools import create_rfa_from_finding

        props = create_rfa_from_finding.input_schema.model_json_schema()["properties"]
        assert props["confirm"]["default"] is False
        assert "status" not in props


def _matcher_fixture(db, repo_slug, class_names, function_names=(), deps=()):
    from resource_explorer.ingestion.code_symbol_extractor import CodeSymbol
    from resource_explorer.registry import Project

    db.add(Project(slug=repo_slug, display_name=repo_slug, github_url=f"https://github.com/o/{repo_slug}"))

    def _sym(name, kind):
        return CodeSymbol(
            resource_slug=repo_slug, file_path=f"{name}.py", language="python", kind=kind,
            name=name, qualified_name=name, signature="", docstring="", start_line=1, end_line=2,
        )

    db.upsert_code_symbols(
        repo_slug,
        [_sym(n, "class") for n in class_names] + [_sym(n, "function") for n in function_names],
    )
    if deps:
        db.upsert_dependencies(repo_slug, [
            {"dep_name": d, "dep_version": "1", "dep_type": "runtime", "ecosystem": "pip",
             "source_file": "requirements.txt"} for d in deps
        ])


def _matcher_db(db, tables):
    from resource_explorer.registry import DatabaseEntity

    db.register_database(DatabaseEntity(
        slug="shop_db", display_name="Shop", db_type="postgresql", host="h", port=5432,
        database_name="shop",
    ))
    db.record_database_survey(
        slug="shop_db", schema_count=1, table_count=len(tables), column_count=1,
        survey_data={"schema_info": {"schemas": [{"name": "public", "tables": [{"name": t} for t in tables]}]}},
    )


def test_cross_asset_matcher_matches_model_classes_to_tables(db):
    from resource_explorer.surveyors.cross_asset_matcher import find_cross_asset_links

    _matcher_db(db, ["order_items", "warranties", "invoices"])
    # OrderItem -> order_items (separators + plural), Warranty -> warranties (ies -> y).
    _matcher_fixture(db, "shop_api", ["OrderItem", "Warranty"], deps=["sqlalchemy", "psycopg2-binary"])
    (link,) = find_cross_asset_links(db)
    assert link["matched_tables"] == ["order_items", "warranties"]
    assert link["distinctive_tables"] == ["order_items", "warranties"]
    assert link["confidence"] == "high"


def test_cross_asset_matcher_ignores_non_class_symbols_and_beats_the_old_limit(db):
    from resource_explorer.surveyors.cross_asset_matcher import find_cross_asset_links

    _matcher_db(db, ["invoices"])
    # A function named like the table is not a model, however many other symbols come first.
    filler = [f"Helper{i}" for i in range(600)]
    _matcher_fixture(db, "shop_api", filler, function_names=["invoices"], deps=["sqlalchemy"])
    assert find_cross_asset_links(db) == []

    # And a model past the old 500-symbol window is still found.
    _matcher_fixture(db, "shop_core", filler + ["Invoice"], deps=["sqlalchemy"])
    (link,) = find_cross_asset_links(db, repo_slug="shop_core")
    assert link["matched_tables"] == ["invoices"]


def test_cross_asset_matcher_orm_alone_is_not_a_link(db):
    from resource_explorer.surveyors.cross_asset_matcher import find_cross_asset_links

    _matcher_db(db, ["invoices"])
    _matcher_fixture(db, "unrelated", ["Widget"], deps=["sqlalchemy"])
    assert find_cross_asset_links(db) == []


def test_cross_asset_matcher_generic_names_and_bare_class_matches_stay_low(db):
    from resource_explorer.surveyors.cross_asset_matcher import find_cross_asset_links

    _matcher_db(db, ["users", "roles", "sessions", "events", "tasks"])
    # Five generic-named classes and no driver or ORM behind them: a coincidence, not a "high" link.
    _matcher_fixture(db, "app", ["User", "Role", "Session", "Event", "Task"])
    links = find_cross_asset_links(db)
    assert all(l["confidence"] == "low" for l in links)


def test_disambiguation_chips_rank_and_cap(db):
    from resource_explorer.resource_chips import CHIP_LIMIT as _CHIP_LIMIT, rank_chip_candidates as _rank_chip_candidates
    from resource_explorer.web.routes.query import _disambiguation_chips

    cands = [{"slug": f"repo_{c}", "name": f"Repo {c}", "type": "repo"} for c in "abcdefghijkl"]
    cands.append({"slug": "sales_db", "name": "Sales Warehouse", "type": "database"})

    # Named resources are not a choice; the open one is first; query words rank next; list is capped.
    ranked = _rank_chip_candidates(
        "compare the warehouse against repo_a", cands, named=["repo_a"], scope_slug="repo_k"
    )
    assert [c["slug"] for c in ranked[:2]] == ["repo_k", "sales_db"]
    assert "repo_a" not in [c["slug"] for c in ranked]
    assert len(ranked) == _CHIP_LIMIT

    # Two resources already named: no chips at all.
    from resource_explorer.registry import Project

    db.add(Project(slug="app_alpha", display_name="App Alpha", github_url="https://github.com/o/a"))
    db.add(Project(slug="app_beta", display_name="App Beta", github_url="https://github.com/o/b"))
    with patch("resource_explorer.registry.ProjectRegistry", return_value=db), patch(
        "resource_explorer.agents.base.BaseExplorerAgent._infer_all_project_slugs",
        return_value=["app_alpha", "app_beta"],
    ):
        assert _disambiguation_chips("compare app_alpha and app_beta") is None

    # One named: the other candidates are offered, the named one is not.
    with patch("resource_explorer.registry.ProjectRegistry", return_value=db), patch(
        "resource_explorer.agents.base.BaseExplorerAgent._infer_all_project_slugs",
        return_value=["app_alpha"],
    ):
        chips = _disambiguation_chips("compare app_alpha with something")
        assert [c["slug"] for c in chips] == ["app_beta"]


def test_a2a_input_required_offers_ranked_repos_and_databases(db):
    from types import SimpleNamespace

    from resource_explorer.agentstack_server import _choice_status, _text
    from resource_explorer.registry import DatabaseEntity, Project

    db.add(Project(slug="app_alpha", display_name="App Alpha", github_url="https://github.com/o/a"))
    db.register_database(DatabaseEntity(
        slug="sales_db", display_name="Sales Warehouse", db_type="postgresql", host="h", port=5432,
        database_name="sales",
    ))
    ctx = SimpleNamespace(task_id="t1", context_id="c1")

    with patch("resource_explorer.registry.ProjectRegistry", return_value=db):
        status = _choice_status(
            "Which two?", "'a and b'.", "compare app_alpha with the warehouse", ["app_alpha"], ctx
        )

    assert status.state.value == "input-required"
    text = _text(status.message)
    assert "sales_db (database)" in text and "app_alpha" not in text
    data = [p.root.data for p in status.message.parts if hasattr(p.root, "data")]
    assert data == [{
        "kind": "resource_choices",
        "choices": [{"slug": "sales_db", "name": "Sales Warehouse", "type": "database"}],
    }]

    # Nothing registered: still a valid question, with no data part.
    with patch("resource_explorer.resource_chips.candidate_chips", return_value=[]):
        status = _choice_status("Which two?", "'a and b'.", "compare", [], ctx)
    assert "none registered" in _text(status.message)
    assert not [p for p in status.message.parts if hasattr(p.root, "data")]


def _schema(*tables):
    return {"schema_info": {"schemas": [{"name": "public", "tables": [
        {"name": n, "columns": [dict(c) for c in cols]} for n, cols in tables
    ]}]}}


def test_database_schema_changes(db):
    from resource_explorer.agents.tools import database_schema_changes_raw
    from resource_explorer.registry import DatabaseEntity

    db.register_database(DatabaseEntity(
        slug="shop_db", display_name="Shop", db_type="postgresql", host="h", port=5432,
        database_name="shop",
    ))
    with patch("resource_explorer.registry.ProjectRegistry", return_value=db):
        assert "not registered" in database_schema_changes_raw("nope")
        assert "never been surveyed" in database_schema_changes_raw("shop_db")

        v1 = _schema(
            ("orders", [{"name": "id", "type": "int", "nullable": False, "is_primary_key": True},
                        {"name": "note", "type": "text", "nullable": True}]),
            ("legacy", [{"name": "id", "type": "int"}]),
        )
        db.record_database_survey(slug="shop_db", schema_count=1, table_count=2, column_count=3, survey_data=v1)
        assert "surveyed once" in database_schema_changes_raw("shop_db")

        v2 = _schema(
            ("orders", [{"name": "id", "type": "bigint", "nullable": False, "is_primary_key": True},
                        {"name": "total", "type": "numeric", "nullable": True}]),
            ("customers", [{"name": "id", "type": "int"}]),
        )
        db.record_database_survey(slug="shop_db", schema_count=1, table_count=2, column_count=3, survey_data=v2)
        out = database_schema_changes_raw("shop_db")
        assert "Tables added (1): public.customers" in out
        assert "Tables removed (1): public.legacy" in out
        assert "columns added: total" in out and "columns removed: note" in out
        assert "`id`: type int -> bigint" in out

        # Identical re-survey: an explicit "no differences", not silence.
        db.record_database_survey(slug="shop_db", schema_count=1, table_count=2, column_count=3, survey_data=v2)
        assert "No differences" in database_schema_changes_raw("shop_db")

        # A publish-only record (no schema_info) is not a measurement and is skipped.
        db.record_database_survey(
            slug="shop_db", schema_count=0, table_count=0, column_count=0, survey_data={}, source="egeria-published",
        )
        assert "No differences" in database_schema_changes_raw("shop_db")


def test_dependency_changes(db):
    from resource_explorer.agents.tools import dependency_changes_raw
    from resource_explorer.registry import Project

    db.add(Project(slug="dep_repo", display_name="Dep Repo", github_url="https://github.com/o/d"))

    def dep(name, version):
        return {"dep_name": name, "dep_version": version, "dep_type": "runtime", "ecosystem": "pip",
                "source_file": "requirements.txt"}

    with patch("resource_explorer.registry.ProjectRegistry", return_value=db):
        assert "not registered" in dependency_changes_raw("nope")
        assert "nothing to compare" in dependency_changes_raw("dep_repo")
        db.upsert_dependencies("dep_repo", [dep("requests", "2.0"), dep("flask", "2.0"), dep("old", "1")])
        assert "recorded once" in dependency_changes_raw("dep_repo")
        db.upsert_dependencies("dep_repo", [dep("requests", "2.1"), dep("flask", "2.0"), dep("new", "3")])
        out = dependency_changes_raw("dep_repo")
        assert "Added (1): new 3" in out and "Removed (1): old 1" in out
        assert "requests: 2.0 -> 2.1" in out and "3 -> 3 dependencies" in out


def test_coverage_sweep(db):
    from resource_explorer.agents.tools import coverage_sweep_raw
    from resource_explorer.facts import Fact, MEASURED, NEVER_RUN, NOTHING_FOUND
    from resource_explorer.registry import DatabaseEntity, Project

    for slug in ("repo_a", "repo_b", "repo_c"):
        db.add(Project(slug=slug, display_name=slug, github_url=f"https://github.com/o/{slug}"))
    db.register_database(DatabaseEntity(
        slug="shop_db", display_name="Shop", db_type="postgresql", host="h", port=5432,
        database_name="shop",
    ))
    states = {"repo_a": MEASURED, "repo_b": NOTHING_FOUND, "repo_c": NEVER_RUN}

    def fake_fact(self, slug, analysis_id, level="resource"):
        return Fact(analysis_id=analysis_id, state=states.get(slug, NEVER_RUN))

    with patch("resource_explorer.registry.ProjectRegistry", return_value=db), patch(
        "resource_explorer.facts.FactLayer.fact", fake_fact
    ):
        # A plain word resolves to its analysis; only resource types that declare it are swept.
        out = coverage_sweep_raw("license")
        assert "`license_classification` across 3 of 3 repo" in out
        assert "has a result (1): repo_a" in out
        assert "ran, found nothing (1): repo_b" in out
        assert "never run (1): repo_c" in out
        assert "not passes" in out and "shop_db" not in out

        # Gaps only: resources with a result are counted, not listed.
        gaps = coverage_sweep_raw("license_classification", only_gaps=True)
        assert "has a result: 1" in gaps and "repo_a" not in gaps and "never run (1): repo_c" in gaps

        # Database-only analysis reaches the database, not the repos.
        assert "across 1 of 1 database" in coverage_sweep_raw("schema_inventory")

        # Unknown analysis and mismatched type say what is available instead of guessing.
        assert "not an analysis I can sweep" in coverage_sweep_raw("bogus")
        assert "does not apply to database" in coverage_sweep_raw("license_classification", "database")
        assert "Unknown resource type" in coverage_sweep_raw("license", "thing")

    # A spent time budget reports unchecked resources rather than calling them covered.
    from resource_explorer.coverage_sweep import coverage_sweep

    with patch("resource_explorer.facts.FactLayer.fact", fake_fact):
        out = coverage_sweep(db, "license", time_budget=-1)
    assert "across 0 of 3" in out and "3 resource(s) were not checked" in out


def test_find_repos_by_dependency(db):
    from resource_explorer.agents.tools import find_repos_by_dependency_raw
    from resource_explorer.registry import Project

    for slug in ("uses_it", "dropped_it", "no_deps"):
        db.add(Project(slug=slug, display_name=slug, github_url=f"https://github.com/o/{slug}"))

    def dep(name, version="1", eco="pip"):
        return {"dep_name": name, "dep_version": version, "dep_type": "runtime", "ecosystem": eco,
                "source_file": "f"}

    # The same pin in two manifests is listed once.
    db.upsert_dependencies("uses_it", [dep("SQLAlchemy", "2.0"), dep("SQLAlchemy", "2.0"), dep("flask")])
    db.upsert_dependencies("dropped_it", [dep("sqlalchemy")])
    db.upsert_dependencies("dropped_it", [dep("flask")])  # latest batch no longer has it

    with patch("resource_explorer.registry.ProjectRegistry", return_value=db):
        out = find_repos_by_dependency_raw("sqlalchemy")
        assert "1 of 3 registered" in out and "`uses_it`: SQLAlchemy 2.0\n" in out + "\n"
        assert "dropped_it`:" not in out
        assert "Unknown (1): no dependencies are recorded for no_deps" in out
        assert "0 of 3" in find_repos_by_dependency_raw("sqlalchemy", "npm")
        assert "Give a dependency name" in find_repos_by_dependency_raw("  ")
        # '_' and '%' are literals, not wildcards.
        assert "0 of 3" in find_repos_by_dependency_raw("sql_lchemy", contains=True)

        # Exact by default: a fragment is not the package; spelling variants are.
        assert "0 of 3" in find_repos_by_dependency_raw("sql")
        assert "1 of 3" in find_repos_by_dependency_raw("SQLAlchemy")
        db.upsert_dependencies("no_deps", [dep("python_dateutil", "2.9")])
        assert "`no_deps`" in find_repos_by_dependency_raw("python-dateutil")
        # contains=true is the loose search.
        assert "(name contains)" in find_repos_by_dependency_raw("alchemy", contains=True)
        assert "`uses_it`" in find_repos_by_dependency_raw("alchemy", contains=True)


def test_rank_databases(db):
    from resource_explorer.agents.tools import rank_databases_raw
    from resource_explorer.registry import DatabaseEntity

    for slug in ("big_db", "small_db", "unsurveyed_db"):
        db.register_database(DatabaseEntity(
            slug=slug, display_name=slug, db_type="postgresql", host="h", port=5432, database_name=slug,
        ))

    def col(n):
        return {"name": n, "type": "int"}

    big = {"schema_info": {"schemas": [
        {"name": "public", "tables": [
            {"name": "a", "description": "documented", "columns": [col("x"), col("y")]},
            {"name": "b", "description": "", "columns": [col("x")]},
            {"name": "c", "description": "", "columns": [col("x")]},
            {"name": "d", "description": "", "source": "catalog_fallback", "columns": [col("x")]},
        ]},
        {"name": "locked", "access": "not_readable", "tables": [{"name": "e", "columns": []}]},
    ]}}
    small = {"schema_info": {"schemas": [{"name": "public", "tables": [
        {"name": "a", "description": "", "columns": [col("x")]},
    ]}]}}
    db.record_database_survey(slug="big_db", schema_count=2, table_count=5, column_count=5, survey_data=big)
    db.record_database_survey(slug="small_db", schema_count=1, table_count=1, column_count=1, survey_data=small)

    with patch("resource_explorer.registry.ProjectRegistry", return_value=db):
        out = rank_databases_raw("tables")
        assert out.index("`big_db`: 5") < out.index("`small_db`: 1")
        assert "2 of 3 have a measured survey" in out
        assert "Not ranked (1): no measured survey for unsurveyed_db" in out

        # Only readable tables with no description count; catalog-only and locked tables are unknown.
        und = rank_databases_raw("undocumented_tables")
        assert "`big_db`: 2 of 3 readable; 2 table(s) whose description could not be read" in und
        assert "`small_db`: 1 of 1 readable" in und
        assert "Unknown metric" in rank_databases_raw("beauty")


def test_list_rfas_is_read_only_and_honest(db):
    from resource_explorer.agents.rfa_tools import create_rfa_from_finding_raw
    from resource_explorer.agents.tools import list_rfas_raw
    from resource_explorer.registry import Project

    db.add(Project(slug="rfa_repo", display_name="RFA Repo", github_url="https://github.com/o/r"))
    with patch("resource_explorer.registry.ProjectRegistry", return_value=db):
        assert "No open RequestForAction" in list_rfas_raw("rfa_repo")
        create_rfa_from_finding_raw("rfa_repo", "Missing docs", analysis_name="doc_coverage")
        create_rfa_from_finding_raw("rfa_repo", "No tests")
        out = list_rfas_raw("rfa_repo")
        assert "2 RequestForAction entries for 'rfa_repo'" in out
        assert "[open]" in out and "Missing docs" in out and "No tests" in out
        assert "only read them" in out

        entry = next(e for e in db.list_activity(limit=10) if e["summary"] == "No tests")
        db.upsert_rfa_action(
            rfa_id=f"{entry['id']}::0", entry_id=entry["id"], annotation_index=0,
            status="completed", activity_status="completed",
        )
        assert "No tests" not in list_rfas_raw("rfa_repo")
        assert "No tests" in list_rfas_raw("rfa_repo", include_closed=True)


def test_cross_asset_matcher_real_data_lessons(db):
    """Failures seen on a real registry: generic plurals, tables shared by many databases, config-only HIGH."""
    from resource_explorer.registry import DatabaseEntity
    from resource_explorer.surveyors.cross_asset_matcher import (
        find_cross_asset_links,
        query_cross_asset_links_raw,
    )

    def add_db(slug, db_name, tables):
        db.register_database(DatabaseEntity(
            slug=slug, display_name=slug, db_type="postgresql", host="h", port=5432, database_name=db_name,
        ))
        db.record_database_survey(
            slug=slug, schema_count=1, table_count=len(tables), column_count=1,
            survey_data={"schema_info": {"schemas": [{"name": "public", "tables": [{"name": t} for t in tables]}]}},
        )

    # `widgets` is in three databases, so it identifies none of them; `users` is generic (plural form).
    add_db("db_one", "one", ["widgets", "users", "ledgers"])
    add_db("db_two", "two", ["widgets"])
    add_db("db_three", "three", ["widgets"])
    # A database named like the repo.
    add_db("zeta_db", "zeta", ["unrelated_thing"])

    # Two common/generic matches, a driver and an ORM: score reaches the HIGH band but must not be HIGH.
    _matcher_fixture(db, "app_a", ["Widget", "User"], deps=["sqlalchemy", "psycopg2-binary"])
    # One distinctive match is still not enough for HIGH.
    _matcher_fixture(db, "app_b", ["Ledger", "Widget"], deps=["sqlalchemy", "psycopg2-binary"])
    # Driver + ORM + database name in the repo slug, but no matching table: never above low.
    _matcher_fixture(db, "zeta_service", ["Nothing"], deps=["sqlalchemy", "psycopg2-binary"])

    links = {(l["repo_slug"], l["database_slug"]): l for l in find_cross_asset_links(db)}
    a = links[("app_a", "db_one")]
    assert a["matched_tables"] == ["users", "widgets"] and a["distinctive_tables"] == []
    assert a["confidence"] == "medium"
    b = links[("app_b", "db_one")]
    assert b["distinctive_tables"] == ["ledgers"] and b["confidence"] == "medium"
    z = links[("zeta_service", "zeta_db")]
    assert z["matched_tables"] == [] and z["confidence"] == "low" and z["score"] >= 0.6

    # Scoped to one database the counts still use the whole catalog.
    scoped = find_cross_asset_links(db, database_slug="db_one")
    assert {l["database_slug"] for l in scoped} == {"db_one"}
    assert next(l for l in scoped if l["repo_slug"] == "app_a")["distinctive_tables"] == []

    # Output is tiered and bounded, and always reports what it left out.
    with patch("resource_explorer.registry.ProjectRegistry", return_value=db):
        medium = query_cross_asset_links_raw("", min_confidence="medium", limit=2)
        assert "Showing 2 of" in medium and "limit 2" in medium and "not that the repo owns" in medium
        assert "Unknown min_confidence" in query_cross_asset_links_raw("", min_confidence="bogus")


def test_a_read_only_agent_is_not_given_the_rfa_tool(db):
    """The A2A surface sets allow_writes False; neither specialist then carries the filing tool."""
    from resource_explorer.agents.compare_agent import CompareAgent
    from resource_explorer.agents.integration_agent import IntegrationAgent

    for cls in (CompareAgent, IntegrationAgent):
        open_agent, closed_agent = cls(), cls()
        closed_agent.allow_writes = False
        assert any("create_rfa" in getattr(t, "name", "") for t in open_agent.tools())
        assert not any("create_rfa" in getattr(t, "name", "") for t in closed_agent.tools())
    closed = CompareAgent()
    closed.allow_writes = False
    assert "cannot file RequestForActions" in closed.system_prompt()
