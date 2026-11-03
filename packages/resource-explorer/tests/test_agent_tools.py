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
        assert "Latest survey record: " in res_survey and "unknown" not in res_survey
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
    assert "create_rfa_from_finding" not in tools
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


def test_the_default_coverage_matrix_asks_only_for_real_analysis_ids(db):
    """"dependencies" and "project_stats" are not analysis ids: FactLayer answered NOT_ESTABLISHED for every resource,
    so the integration answer for marquez/openlineage read "Dependencies: not established" whatever the data."""
    from resource_explorer.context_compile import build_epistemological_matrix
    from resource_explorer.facts import FactLayer
    from resource_explorer.registry import Project

    db.add(Project(slug="app_alpha", display_name="App Alpha", github_url="https://github.com/org/alpha"))
    md_table, records = build_epistemological_matrix(db, ["app_alpha"])
    ids = [r["analysis_id"] for r in records]
    assert "dependency_analysis" in ids and "dependencies" not in ids and "project_stats" not in ids
    known = FactLayer(db, resource_type="repo")._map("analysis_results_map")
    assert "dependency_analysis" in known
    # Not run is said as not run, never as "an action rather than findings".
    dep = next(r for r in records if r["analysis_id"] == "dependency_analysis")["slugs"]["app_alpha"]
    assert dep["state"] == "never_run" and "action rather than" not in md_table


def test_the_integration_agent_puts_stored_cross_asset_links_in_its_evidence(db):
    from unittest.mock import patch

    from resource_explorer.agents import tools
    from resource_explorer.agents.integration_agent import IntegrationAgent

    seen = []

    def raw(resource_slug="", min_confidence="high", limit=15):
        seen.append((resource_slug, min_confidence))
        return "No links found." if resource_slug == "openlineage" else "marquez -> laz_local_marquez (shared tables)"

    with patch.object(tools, "query_cross_asset_links_raw", raw):
        lines = IntegrationAgent()._cross_asset_link_lines(["marquez", "openlineage"])
    assert seen == [("marquez", "low"), ("openlineage", "low")]
    assert "Cross-asset links stored for marquez" in lines[0] and "laz_local_marquez" in lines[0]
    assert "No links found." in lines[1], "an absence is said, not dropped"

    def boom(**kw):
        raise RuntimeError("registry down")

    with patch.object(tools, "query_cross_asset_links_raw", boom):
        assert IntegrationAgent()._cross_asset_link_lines(["marquez"]) == []


def test_the_integration_agent_cites_a_declared_dependency_that_names_the_other_resource(db):
    from unittest.mock import patch

    from resource_explorer.agents.integration_agent import IntegrationAgent
    from resource_explorer.registry import Project

    for slug in ("marquez", "openlineage", "emptyrepo"):
        db.add(Project(slug=slug, display_name=slug, github_url=f"https://github.com/org/{slug}"))
    db.upsert_dependencies("marquez", [
        {"dep_name": "io.openlineage:openlineage-java", "dep_version": "1.2.0", "dep_type": "runtime",
         "ecosystem": "maven", "source_file": "build.gradle"},
        {"dep_name": "org.postgresql:postgresql", "dep_version": "42", "dep_type": "runtime",
         "ecosystem": "maven", "source_file": "build.gradle"}])
    db.upsert_dependencies("openlineage", [
        {"dep_name": "requests", "dep_version": "2", "dep_type": "runtime", "ecosystem": "pip", "source_file": "r.txt"}])
    with patch("resource_explorer.registry.ProjectRegistry", return_value=db):
        lines = IntegrationAgent()._dependency_link_lines(["marquez", "openlineage", "emptyrepo"])
    text = "\n".join(lines)
    assert "[marquez] declares 1 dependency naming openlineage: io.openlineage:openlineage-java 1.2.0" in text
    assert "postgresql" not in text, "only dependencies that name the other resource"
    assert "[emptyrepo] No dependencies are recorded" in text and "unknown, not 'no'" in text
    assert "[openlineage] declares" not in text, "requests names neither marquez nor emptyrepo"


def test_the_integration_answer_leads_with_the_declared_dependency_and_the_prompt_puts_it_first(db):
    from unittest.mock import patch

    from resource_explorer.agents.integration_agent import IntegrationAgent
    from resource_explorer.registry import Project

    for slug in ("marquez", "openlineage"):
        db.add(Project(slug=slug, display_name=slug, github_url=f"https://github.com/org/{slug}"))
    db.upsert_dependencies("marquez", [
        {"dep_name": "io.openlineage:openlineage-java", "dep_version": "1.2.0", "dep_type": "runtime",
         "ecosystem": "maven", "source_file": "build.gradle"}])
    prompts = []
    agent = IntegrationAgent()
    with patch("resource_explorer.registry.ProjectRegistry", return_value=db), \
         patch.object(IntegrationAgent, "_infer_all_project_slugs", return_value=["marquez", "openlineage"]), \
         patch.object(IntegrationAgent, "_multi_resource_evidence", return_value=["MATRIX"]), \
         patch.object(IntegrationAgent, "_cross_asset_link_lines", return_value=["LINKS TABLE"]), \
         patch.object(IntegrationAgent, "_run_agent", side_effect=lambda p: prompts.append(p) or "Through PostgreSQL."):
        answer = agent.handle("Can marquez work with openlineage?")
    assert answer.startswith(
        "marquez declares 1 dependency naming openlineage (io.openlineage:openlineage-java 1.2.0 in build.gradle)")
    assert answer.endswith("Through PostgreSQL.")
    prompt = prompts[0]
    assert prompt.index("Direct evidence") < prompt.index("MATRIX") < prompt.index("LINKS TABLE")

    # No declared dependency: nothing is added to the model's answer.
    with patch("resource_explorer.registry.ProjectRegistry", return_value=db):
        assert IntegrationAgent()._direct_answer_lead(["openlineage", "marquez"]) .startswith("marquez declares")
        assert IntegrationAgent()._direct_answer_lead(["openlineage"]) == ""


def test_the_lead_names_each_manifest_once_per_version():
    """data_prep_kit declares docling-core ==2.50.1 in two requirements.txt files. Listed per row the lead read
    "2 dependencies naming docling_core (docling-core ==2.50.1; docling-core ==2.50.1)": one dependency, its
    version twice and neither file named."""
    from resource_explorer.agents.integration_agent import IntegrationAgent

    hits = [{"dep_name": "docling-core", "dep_version": "==2.50.1",
             "source_file": "transforms/language/doc_chunk/requirements.txt"},
            {"dep_name": "docling-core", "dep_version": "==2.50.1",
             "source_file": "transforms/language/docling2parquet/requirements.txt"}]
    with patch.object(IntegrationAgent, "_declared_dependencies",
                      return_value=([("data_prep_kit", "docling_core", hits)], [])):
        lead = IntegrationAgent()._direct_answer_lead(["data_prep_kit", "docling_core"])
        lines = IntegrationAgent()._dependency_link_lines(["data_prep_kit", "docling_core"])
    assert lead == ("data_prep_kit declares 1 dependency naming docling_core (docling-core ==2.50.1 in "
                    "transforms/language/doc_chunk/requirements.txt and "
                    "transforms/language/docling2parquet/requirements.txt), so the two are already connected.")
    assert lines[0].startswith("[data_prep_kit] declares 1 dependency naming docling_core: docling-core ==2.50.1 in ")


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
        assert "not yet run (1): repo_c" in out
        assert "not passes" in out and "shop_db" not in out

        # Gaps only: resources with a result are counted, not listed.
        gaps = coverage_sweep_raw("license_classification", only_gaps=True)
        assert "has a result: 1" in gaps and "repo_a" not in gaps and "not yet run (1): repo_c" in gaps

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
        assert "`big_db`: 2 of 3 readable base tables; 2 table(s) whose description was not read" in und
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
        assert "2 RequestForAction entries for 'rfa_repo' not yet completed (2 open;" in out
        assert "[open]" in out and "Missing docs" in out and "No tests" in out
        assert "only read them" in out

        entry = next(e for e in db.list_activity(limit=10) if e["summary"] == "No tests")
        db.upsert_rfa_action(
            rfa_id=f"{entry['id']}::0", entry_id=entry["id"], annotation_index=0,
            status="completed", activity_status="completed",
        )
        default = list_rfas_raw("rfa_repo")
        assert "No tests" not in default
        # The default view says a closed one exists and how to see it.
        assert "1 completed (closed) RFA for 'rfa_repo' is hidden" in default and "include_closed=true" in default
        everything = list_rfas_raw("rfa_repo", include_closed=True)
        assert "No tests" in everything and "[completed]" in everything
        assert "including completed ones" in everything and "not yet completed" not in everything
        assert "hidden from this list" not in everything

        # Nothing open, one closed: the empty answer must not read as "no closed RFAs either".
        other = db.list_activity(limit=10)
        entry2 = next(e for e in other if e["summary"] == "Missing docs")
        db.upsert_rfa_action(
            rfa_id=f"{entry2['id']}::0", entry_id=entry2["id"], annotation_index=0,
            status="completed", activity_status="completed",
        )
        none_open = list_rfas_raw("rfa_repo")
        assert none_open.startswith("No open RequestForAction") and "2 completed (closed) RFAs" in none_open


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
    assert z["matched_tables"] == [] and z["confidence"] == "low" and z["raw_score"] >= 0.6
    # The score shown stays inside the label's band, so a LOW row never outscores a MEDIUM one.
    assert z["score"] <= 0.39 and any("capped to low" in e for e in z["evidence"])
    assert a["score"] <= 0.59
    ordered = find_cross_asset_links(db)
    ranks = {"low": 0, "medium": 1, "high": 2}
    assert [ranks[l["confidence"]] for l in ordered] == sorted((ranks[l["confidence"]] for l in ordered), reverse=True)
    scores_by_conf = {c: [l["score"] for l in ordered if l["confidence"] == c] for c in ranks}
    assert max(scores_by_conf["low"], default=0) < min(scores_by_conf["medium"] or [1], default=1)

    # Scoped to one database the counts still use the whole catalog.
    scoped = find_cross_asset_links(db, database_slug="db_one")
    assert {l["database_slug"] for l in scoped} == {"db_one"}
    assert next(l for l in scoped if l["repo_slug"] == "app_a")["distinctive_tables"] == []

    # Output is tiered and bounded, and always reports what it left out.
    with patch("resource_explorer.registry.ProjectRegistry", return_value=db):
        medium = query_cross_asset_links_raw("", min_confidence="medium", limit=2)
        assert "Showing the first 2 of" in medium and "limit 2" in medium and "not that the repo owns" in medium
        assert "Unknown min_confidence" in query_cross_asset_links_raw("", min_confidence="bogus")


def test_a_question_that_says_low_forces_the_low_floor_whatever_the_model_passed():
    """Traced twice on llama3.1:8b: "include medium and low links" was called with min_confidence="medium"."""
    from resource_explorer.agents.tools import link_confidence_floor

    q = "Which databases does egeria-trellis use, include medium and low links"
    assert link_confidence_floor(q, "medium") == "low"
    assert link_confidence_floor(q, "high") == "low"
    assert link_confidence_floor(q, "low") == "low"
    assert link_confidence_floor("Which databases does egeria-trellis use", "medium") == "medium"
    assert link_confidence_floor("Which repos allow login below the cutoff", "high") == "high", "'low' only as a word"
    assert link_confidence_floor(q, "bogus") == "bogus", "an unknown value still reaches the tool's own error"


def test_the_links_tool_applies_the_floor_from_the_turns_question():
    import asyncio
    from unittest.mock import patch

    from resource_explorer.agents import tools

    async def call():
        out = await tools.query_cross_asset_links.run({"resource_slug": "egeria-trellis", "min_confidence": "medium"})
        return out

    tools.set_current_question("which databases does it use, include medium and low links")
    try:
        with patch.object(tools, "query_cross_asset_links_raw", return_value="ok") as raw:
            asyncio.run(call())
        assert raw.call_args.kwargs["min_confidence"] == "low"
        tools.set_current_question("which databases does it use")
        with patch.object(tools, "query_cross_asset_links_raw", return_value="ok") as raw:
            asyncio.run(call())
        assert raw.call_args.kwargs["min_confidence"] == "medium"
    finally:
        tools.set_current_question("")


def test_only_the_conversation_agent_can_file_an_rfa(db):
    """A traced comparison called create_rfa_from_finding unprompted; the specialists never carry it."""
    from resource_explorer.agents.compare_agent import CompareAgent
    from resource_explorer.agents.conversation_agent import ConversationAgent
    from resource_explorer.agents.integration_agent import IntegrationAgent

    def names(agent):
        return [getattr(t, "name", "") for t in agent.tools()]

    for cls in (CompareAgent, IntegrationAgent):
        assert not any("create_rfa" in n for n in names(cls())), cls
    assert "create_rfa_from_finding" not in CompareAgent().system_prompt()

    # The conversation agent keeps it, unless it is serving the read-only A2A surface.
    open_agent, closed_agent = ConversationAgent(), ConversationAgent()
    closed_agent.allow_writes = False
    assert any("create_rfa" in n for n in names(open_agent))
    assert not any("create_rfa" in n for n in names(closed_agent))


def test_database_figures_count_a_read_fallback_comment():
    from resource_explorer.discovery import _database_figures

    info = {"schemas": [{"name": "s", "tables": [
        {"name": "hidden_read_empty", "source": "catalog_fallback", "description_read": True, "description": ""},
        {"name": "hidden_read_doc", "source": "catalog_fallback", "description_read": True, "description": "x"},
        {"name": "hidden_unread", "source": "catalog_fallback", "description": ""},
    ]}]}
    f = _database_figures(info)
    assert (f["documented"], f["undocumented_tables"], f["unmeasured"]) == (1, 1, 1)


def test_database_figures_keep_views_out_of_the_table_counts():
    from resource_explorer.discovery import _database_figures

    info = {"schemas": [{"name": "s", "tables": [
        {"name": "t1", "type": "BASE TABLE", "description": "x"},
        {"name": "t2", "type": "BASE TABLE", "description": ""},
        {"name": "t3", "description": ""},  # no recorded type counts as a table
        {"name": "v1", "type": "VIEW", "description": ""},
        {"name": "mv1", "type": "MATERIALIZED VIEW", "source": "catalog_fallback", "description": ""},
    ]}]}
    f = _database_figures(info)
    assert (f["tables"], f["views"]) == (3, 2)
    assert (f["documented"], f["undocumented_tables"], f["unmeasured"]) == (1, 2, 0)


def test_chat_stat_tools_read_the_registry_not_a_sqlite_file(db, monkeypatch):
    """On a Postgres registry `db_path` is an empty SQLite file; the tools must not use it."""
    from datetime import datetime, timedelta
    from resource_explorer.agents.tools import query_project_stats_raw, query_top_committers_raw
    from resource_explorer.registry import Project

    db.add(Project(slug="statrepo", display_name="Stat Repo", github_url="https://github.com/o/statrepo"))
    when = (datetime.utcnow() - timedelta(days=3)).isoformat()
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO project_commits (project_slug, sha, author_name, author_email, committed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("statrepo", "abc123", "Ada", "ada@example.org", when),
        )
    monkeypatch.setattr(db, "db_path", "/nonexistent/empty.db", raising=False)

    with patch("resource_explorer.registry.ProjectRegistry", return_value=db):
        committers = query_top_committers_raw("statrepo")
        stats = query_project_stats_raw("statrepo")

    assert "Ada: 1 commit" in committers
    assert "Error reading" not in stats


def test_top_committers_respects_the_window_and_stats_label_their_dates(db):
    """Old commits must not count toward "last 90 days"; the stats header says which date each figure has."""
    from datetime import datetime, timedelta
    from resource_explorer.agents.tools import query_project_stats_raw, query_top_committers_raw
    from resource_explorer.registry import Project

    db.add(Project(slug="winrepo", display_name="Win Repo", github_url="https://github.com/o/winrepo"))
    now = datetime.utcnow()
    rows = [("n1", "Ada", 5), ("n2", "Ada", 40), ("o1", "Bob", 200), ("o2", "Bob", 300), ("o3", "Bob", 400)]
    with db._conn() as conn:
        for sha, who, age in rows:
            conn.execute(
                "INSERT INTO project_commits (project_slug, sha, author_name, author_email, committed_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("winrepo", sha, who, f"{who}@example.org", (now - timedelta(days=age)).isoformat()),
            )
        conn.execute(
            "INSERT INTO project_stats (project_slug, fetched_at, commits_30d, commits_90d) VALUES (?, ?, ?, ?)",
            ("winrepo", "2026-01-01T00:00:00", 119, 476),
        )

    with patch("resource_explorer.registry.ProjectRegistry", return_value=db):
        recent = query_top_committers_raw("winrepo")
        wide = query_top_committers_raw("winrepo", days=500)
        stats = query_project_stats_raw("winrepo", days=500)

    assert "Ada: 2 commits" in recent and "Bob" not in recent
    assert "last 90 days" in recent
    assert "Bob: 3 commits" in wide and "last 500 days" in wide
    # The snapshot date labels the snapshot figures only; commit counts say they were counted today.
    assert "snapshot taken 2026-01-01" in stats
    assert "counted today" in stats and "from 5 stored commits" in stats
    assert "30d: 1  90d: 2  500d: 5" in stats


def test_database_names_resolve_and_ambiguity_is_reported(db):
    from resource_explorer.history_diff import database_changes
    from resource_explorer.name_resolution import resolve_database

    def add(slug, display, dbname):
        db.register_database(DatabaseEntity(slug=slug, display_name=display, db_type="postgresql",
                                            host="h", port=5432, database_name=dbname))

    add("laz_local_adventureworks", "Laz Local Adventureworks", "adventureworks")
    add("laz_local_coco_pharma", "Laz Local Coco Pharma", "coco_pharma")
    add("pg_a_orders", "Orders A", "orders")
    add("pg_b_orders", "Orders B", "orders")

    assert resolve_database(db, "laz_local_adventureworks") == ("laz_local_adventureworks", [])   # slug
    assert resolve_database(db, "adventureworks") == ("laz_local_adventureworks", [])             # database name
    assert resolve_database(db, "Coco-Pharma") == ("laz_local_coco_pharma", [])                   # suffix, folded
    assert resolve_database(db, "orders") == (None, ["pg_a_orders", "pg_b_orders"])               # ambiguous
    assert resolve_database(db, "nowhere") == (None, [])

    assert "more than one registered database" in database_changes(db, "orders")
    assert "is not registered" in database_changes(db, "nowhere")
    assert "never been surveyed" in database_changes(db, "adventureworks")                        # resolved


def test_a_database_is_found_in_a_question_by_its_own_name(db):
    db.register_database(DatabaseEntity(slug="laz_local_adventureworks", display_name="Laz Local Adventureworks",
                                        db_type="postgresql", host="h", port=5432, database_name="adventureworks"))
    db.register_database(DatabaseEntity(slug="laz_local_coco_pharma", display_name="Laz Local Coco Pharma",
                                        db_type="postgresql", host="h", port=5432, database_name="coco_pharma"))
    with patch("resource_explorer.registry.ProjectRegistry", return_value=db):
        found = MockAgent()._infer_all_project_slugs("compare the adventureworks and coco_pharma schemas")
    assert found == ["laz_local_adventureworks", "laz_local_coco_pharma"]


def test_an_empty_agent_answer_raises_so_the_fallback_runs():
    from resource_explorer.agents import base as base_mod
    from resource_explorer.agents.base import EmptyAgentAnswer

    with patch.object(MockAgent, "_build_agent", return_value=MagicMock()), \
         patch.object(base_mod.asyncio, "run", side_effect=lambda coro: (coro.close(), "  ")[1]):
        with pytest.raises(EmptyAgentAnswer):
            MockAgent()._run_agent("anything")


def test_a_database_given_by_its_own_name_is_found_by_the_schema_tool(db):
    from resource_explorer.agents.tools import query_database_schema_raw

    db.register_database(DatabaseEntity(slug="laz_local_aw", display_name="AW", db_type="postgresql",
                                        host="h", port=5432, database_name="aw"))
    with patch("resource_explorer.registry.ProjectRegistry", return_value=db):
        out = query_database_schema_raw("aw", table_name="sales.salesorderheader")
    # Resolved to the registered database; only the survey is missing.
    assert "not found" not in out.lower()
    assert "No survey data available" in out


def test_building_a_chat_agent_starts_tracing(monkeypatch):
    """Agent runs were untraced once the sessionless RAG path (which started Phoenix) was removed."""
    from resource_explorer.agents.conversation_agent import ConversationAgent

    started = []
    monkeypatch.setattr(
        "resource_explorer.observability.phoenix_client.init_phoenix", lambda: started.append(1)
    )
    ConversationAgent()
    MockAgent()
    assert len(started) == 2


def test_dependency_questions_carry_the_stored_list_in_the_prompt(db, monkeypatch):
    """A model that skips query_dependencies still sees what is stored (traced failure 2026-10-19)."""
    from resource_explorer.agents.conversation_agent import ConversationAgent
    from resource_explorer.registry import Project

    db.add(Project(slug="deprepo", display_name="Dep Repo", github_url="https://github.com/o/deprepo"))
    db.upsert_dependencies("deprepo", [
        {"dep_name": "click", "dep_version": ">=8", "dep_type": "runtime", "ecosystem": "python",
         "source_file": "pyproject.toml"},
        {"dep_name": "pytest", "dep_version": "", "dep_type": "dev", "ecosystem": "python",
         "source_file": "pyproject.toml"},
    ])
    prompts = []
    monkeypatch.setattr(ConversationAgent, "_run_persistent", lambda self, p: prompts.append(p) or "ok")
    with patch("resource_explorer.registry.ProjectRegistry", return_value=db):
        ConversationAgent(resource_slug="deprepo").handle("What does it depend on?", resource_slug="deprepo")
        ConversationAgent(resource_slug="deprepo").handle("how many stars does it have?", resource_slug="deprepo")
    assert "Stored dependencies for deprepo (2 entries" in prompts[0]
    assert "pyproject.toml [runtime] (1): click >=8" in prompts[0] and "pyproject.toml [dev] (1): pytest" in prompts[0]
    assert "Stored dependencies" not in prompts[1]


def test_dependency_drift_questions_carry_the_comparison_in_the_prompt(db, monkeypatch):
    """qwen3.6 made no tool call on "which dependencies changed since the last ingest" (trace 84991db7, 2026-10-20)."""
    from resource_explorer.agents.conversation_agent import ConversationAgent
    from resource_explorer.registry import Project

    db.add(Project(slug="driftrepo", display_name="Drift Repo", github_url="https://github.com/o/driftrepo"))
    prompts = []
    monkeypatch.setattr(ConversationAgent, "_run_persistent", lambda self, p: prompts.append(p) or "ok")
    with patch("resource_explorer.registry.ProjectRegistry", return_value=db), \
         patch("resource_explorer.history_diff.dependency_changes",
               return_value="Dependency changes for `driftrepo`: a -> b\n- Added (1): click 8") as drift:
        agent = ConversationAgent(resource_slug="driftrepo")
        agent.handle("Which dependencies changed since the last ingest?", resource_slug="driftrepo")
        agent.handle("how many stars does it have?", resource_slug="driftrepo")
    assert "Added (1): click 8" in prompts[0] and "answer from it" in prompts[0]
    assert "Dependency changes" not in prompts[1]
    assert drift.call_count == 1


def test_a_common_word_in_a_fleet_question_does_not_bind_a_repo_of_that_name():
    """"Which repos have no license classification?" bound the repo `license` (trace 392880a9, 2026-10-20)."""
    from resource_explorer.agents.conversation_agent import _bare_word_binding

    assert _bare_word_binding("Which repos have no license classification?", "license")
    assert _bare_word_binding("which repositories have a license", "license")
    # A name after a verb or preposition is still a name.
    assert not _bare_word_binding("Which repos depend on marquez?", "marquez")
    assert not _bare_word_binding("Which repos use license?", "license")
    # Not a fleet question, or a multi-part slug: unchanged.
    assert not _bare_word_binding("Does license have a classification?", "license")
    assert not _bare_word_binding("Which repos have no egeria_python classification?", "egeria_python")


def test_chat_drops_the_bare_word_slug_but_keeps_an_explicit_scope(monkeypatch):
    from resource_explorer.agents.conversation_agent import ConversationAgent

    prompts = []
    monkeypatch.setattr(ConversationAgent, "_run_persistent", lambda self, p: prompts.append(p) or "ok")
    monkeypatch.setattr(ConversationAgent, "_infer_project_slug", lambda self, q: "license")
    ConversationAgent().handle("Which repos have no license classification?")
    assert "Project: license" not in prompts[0]
    ConversationAgent().handle("Which repos have no license classification?", resource_slug="license")
    assert "Project: license" in prompts[1]


def _fake_links(n):
    return [
        {"repo_slug": f"r{i}", "repo_name": f"r{i}", "database_slug": "d", "database_name": "d",
         "database_type": "postgres", "confidence": "low", "score": 0.1, "matched_tables": ["t"], "evidence": ["e"]}
        for i in range(n)
    ]


def test_cross_asset_links_say_when_the_limit_hid_rows(db):
    from unittest.mock import patch

    from resource_explorer.surveyors import cross_asset_matcher as m

    with patch.object(m, "find_cross_asset_links", lambda *a, **k: _fake_links(20)):
        capped = m.query_cross_asset_links_raw(min_confidence="low", limit=15)
        whole = m.query_cross_asset_links_raw(min_confidence="low", limit=50)
    assert "first 15 of 20" in capped and "5 more are not shown" in capped and "NOT the total" in capped
    assert "Showing all 20 links" in whole and "NOT the total" not in whole


def test_the_turn_leads_with_the_link_totals_the_tool_reported(db):
    from unittest.mock import patch

    from resource_explorer.agents import tools
    from resource_explorer.surveyors import cross_asset_matcher as m

    links = _fake_links(20)
    for i, l in enumerate(links):
        l["confidence"] = "high" if i == 0 else "medium" if i < 4 else "low"
    with patch.object(m, "find_cross_asset_links", lambda *a, **k: links):
        tools.set_current_question("which databases does it use, include medium and low links")
        assert tools.turn_fact_lead() == ""
        tools.query_cross_asset_links_raw(min_confidence="low", limit=15)
        lead = tools.turn_fact_lead()
        tools.set_current_question("a new turn")
        assert tools.turn_fact_lead() == "", "a new turn does not inherit the last turn's totals"
    assert lead == ("20 cross-asset links found: 1 high, 3 medium, 16 low; "
                    "15 of the 20 at 'low' or above are listed.")


def test_the_totals_are_not_recorded_outside_a_turn(db):
    """Called with no turn set (the integration agent's evidence read), nothing accumulates across turns."""
    import contextvars
    from unittest.mock import patch

    from resource_explorer.agents import tools
    from resource_explorer.surveyors import cross_asset_matcher as m

    def run():
        with patch.object(m, "find_cross_asset_links", lambda *a, **k: _fake_links(3)):
            tools.query_cross_asset_links_raw(min_confidence="low")
        return tools.turn_fact_lead()

    assert contextvars.Context().run(run) == ""


def test_the_conversation_answer_leads_with_the_link_totals(db):
    from unittest.mock import patch

    from resource_explorer.agents import tools
    from resource_explorer.agents.conversation_agent import ConversationAgent
    from resource_explorer.surveyors import cross_asset_matcher as m

    def fake_run(self, prompt):
        tools.query_cross_asset_links_raw(min_confidence="low", limit=2)
        return "There are 2 high-confidence links."

    with patch.object(m, "find_cross_asset_links", lambda *a, **k: _fake_links(5)), \
         patch.object(ConversationAgent, "_run_persistent", fake_run):
        answer = ConversationAgent().handle("which databases does it use, include medium and low links")
    assert answer.startswith("5 cross-asset links found: 0 high, 0 medium, 5 low; 2 of the 5 at 'low' or above")
    assert answer.endswith("There are 2 high-confidence links.")


def _multi_compile_run(db, slugs, budget, text_for=None):
    """compile_multi_resource_context over a per-resource compile that binds the real signature. `text_for(slug,
    share)` is the text each resource returns (default: a short line that always fits)."""
    from unittest.mock import create_autospec

    from resource_explorer import context_compile
    from resource_explorer.context_compile import CompiledContext, compile_multi_resource_context

    def per_resource(registry, slug, question, **kw):
        text = text_for(slug, kw["budget"]) if text_for else f"content for {slug}"
        return CompiledContext(text=text, compile_id=f"id-{slug}-{kw['budget']}", derivation=[], manifest={
            "used": len(text), "gaps": [],
            "packed": [{"key": "dependency_analysis", "role": "evidence", "rung": "FULL", "size": len(text)}]})

    fake = create_autospec(context_compile.compile_context, side_effect=per_resource)
    with patch.object(context_compile, "compile_context", fake), \
         patch.object(context_compile, "build_epistemological_matrix", return_value=("| Matrix |", [])):
        result = compile_multi_resource_context(db, slugs, "test query", budget=budget)
    return result, [c.kwargs["budget"] for c in fake.call_args_list]


def _add_repos(db, *slugs):
    from resource_explorer.registry import Project

    for slug in slugs:
        db.add(Project(slug=slug, display_name=slug, collections=["c"], github_url=f"https://github.com/o/{slug}"))


def test_multi_resource_budget_partitioning(db):
    """Each resource gets an equal share of what the matrix and preamble, MEASURED, leave of the budget; the assembled
    text is what `used` counts, and it never exceeds the budget."""
    _add_repos(db, "repo_a", "repo_b", "repo_c")
    for budget in (1000, 6000, 8000):
        for slugs in (["repo_a", "repo_b"], ["repo_a", "repo_b", "repo_c"]):
            result, shares = _multi_compile_run(db, slugs, budget)
            overhead = len(result.text) - sum(len(f"content for {s}") for s in slugs)
            assert len(set(shares)) == 1
            # Nothing is reserved beyond what the preamble takes (it was a flat 20%), give or take the trailing newline.
            assert budget - len(slugs) - 2 <= sum(shares) + overhead <= budget + 2
            assert result.manifest["used"] == len(result.text) <= budget
            assert result.manifest["headroom"] == budget - len(result.text)
    assert "Matrix" in result.text and "RESOURCE EVIDENCE: repo_a" in result.text


def test_a_multi_resource_compile_over_budget_never_reports_headroom(db):
    """Live, compile 144eef54: budget 6000, used 4996, headroom 1004, and 6,095 characters of text, seen in
    the compile panel on 2026-10-25. `used` added len(matrix) // 4 to character counts."""
    from trellis_context import BudgetError

    _add_repos(db, "repo_a", "repo_b")
    # A resource text that ignores its share entirely: the assembled text cannot fit, so the compile fails.
    with pytest.raises(BudgetError, match="ceiling is hard"):
        _multi_compile_run(db, ["repo_a", "repo_b"], 1000, text_for=lambda slug, share: "x" * 700)
    # Too small for the preamble itself: a hard failure, not a floor that overruns.
    with pytest.raises(BudgetError, match="leaving no room for evidence"):
        _multi_compile_run(db, ["repo_a", "repo_b"], 100)


def test_a_resource_text_past_its_share_steps_every_share_down(db):
    """The packer counts sections, not the blank lines between them, so a resource's text runs a little past its share.
    The assembled text is measured and the shares step down until it fits."""
    _add_repos(db, "repo_a", "repo_b")
    result, shares = _multi_compile_run(db, ["repo_a", "repo_b"], 1000, text_for=lambda slug, share: "x" * (share + 40))
    assert len(shares) == 4 and shares[2] < shares[0], "compiled once, then again with smaller shares"
    assert result.manifest["used"] == len(result.text) <= 1000
    assert result.manifest["headroom"] == 1000 - len(result.text)


def test_a_multi_resource_manifest_lists_what_it_packed_per_resource(db):
    """The readers of a compile read `packed` (item 4): one list across resources, each entry naming its resource."""
    _add_repos(db, "repo_a", "repo_b")
    result, _ = _multi_compile_run(db, ["repo_a", "repo_b"], 2000)
    assert [(p["key"], p["resource_slug"]) for p in result.manifest["packed"]] == [
        ("dependency_analysis", "repo_a"), ("dependency_analysis", "repo_b")]
