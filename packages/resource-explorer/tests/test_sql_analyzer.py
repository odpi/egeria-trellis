import pytest
from unittest import mock
from resource_explorer.surveyors.database.sql_analyzer import SqlAnalyzer

# Brief I: these tests fake the Egeria clients; the code under test runs as a signed-in caller, as on a route.
pytestmark = pytest.mark.usefixtures("signed_in_caller")

def test_parse_dependencies():
    sql = """
    CREATE VIEW report AS
    SELECT u.id, u.name, o.total, p.product_name
    FROM my_schema.users u
    JOIN orders o ON u.id = o.user_id
    LEFT JOIN my_schema.products p ON o.product_id = p.id
    WHERE u.active = true;
    """
    deps = SqlAnalyzer.parse_dependencies(sql)
    
    # Assert all target table references are resolved as dependencies
    assert "my_schema.users" in deps
    assert "orders" in deps
    assert "my_schema.products" in deps
    # The view being created might also be returned as Table in AST
    assert len(deps) >= 3

def test_compute_complexity_metrics():
    sql_simple = "SELECT id, name FROM users;"
    metrics_simple = SqlAnalyzer.compute_complexity_metrics(sql_simple)
    
    assert metrics_simple["join_count"] == 0
    assert metrics_simple["cte_count"] == 0
    assert metrics_simple["complexity_score"] < 20
    assert metrics_simple["portability_rating"] == 100
    
    # Complex SQL query with multiple joins and CTEs
    sql_complex = """
    WITH user_spend AS (
        SELECT user_id, SUM(amount) as total_amount
        FROM orders
        GROUP BY user_id
    )
    SELECT u.id, u.name, s.total_amount, d.department_name
    FROM users u
    JOIN user_spend s ON u.id = s.user_id
    LEFT JOIN departments d ON u.dept_id = d.id;
    """
    metrics_complex = SqlAnalyzer.compute_complexity_metrics(sql_complex)
    
    assert metrics_complex["join_count"] == 2
    assert metrics_complex["cte_count"] == 1
    assert metrics_complex["complexity_score"] > metrics_simple["complexity_score"]
    
    # Portability check: mock transpile failure to verify rating degradation
    with mock.patch("resource_explorer.surveyors.database.sql_analyzer.transpile", side_effect=Exception("transpilation failed")):
        metrics_unportable = SqlAnalyzer.compute_complexity_metrics(sql_simple)
        assert metrics_unportable["portability_rating"] == 50

def test_trace_column_lineage():
    # Define a view query to trace column-level lineage
    view_sql = """
    SELECT u.username AS user_name, o.amount AS order_amount
    FROM users u
    JOIN orders o ON u.id = o.user_id;
    """
    
    schema = {
        "users": {"id": "INT", "username": "VARCHAR"},
        "orders": {"id": "INT", "user_id": "INT", "amount": "DECIMAL"}
    }
    
    lineage_user_name = SqlAnalyzer.trace_column_lineage(
        sql=view_sql,
        target_column="user_name",
        schema=schema,
        db_type="postgres"
    )
    
    assert lineage_user_name is not None
    assert lineage_user_name["name"] == "user_name"
    assert len(lineage_user_name["downstream"]) == 1
    assert "users" in lineage_user_name["downstream"][0]["source"]
    assert "username" in lineage_user_name["downstream"][0]["name"]
    
    lineage_order_amount = SqlAnalyzer.trace_column_lineage(
        sql=view_sql,
        target_column="order_amount",
        schema=schema,
        db_type="postgres"
    )
    
    assert lineage_order_amount is not None
    assert lineage_order_amount["name"] == "order_amount"
    assert len(lineage_order_amount["downstream"]) == 1
    assert "orders" in lineage_order_amount["downstream"][0]["source"]
    assert "amount" in lineage_order_amount["downstream"][0]["name"]

def test_pii_propagation_annotation():
    from resource_explorer.registry import DatabaseEntity
    from resource_explorer.surveyors.database.database_surveyor import DatabaseSurveyor
    from resource_explorer.surveyors.survey_report import AnnotationType
    
    db_entity = DatabaseEntity(
        slug="test-db",
        display_name="Test DB",
        db_type="postgresql",
        host="localhost",
        port=5432,
        database_name="test-db",
    )
    
    surveyor = DatabaseSurveyor(db_entity, {}, None)
    
    views_info = [{
        "name": "user_report",
        "qualified_name": "public.user_report",
        "dependencies": ["users", "orders"],
        "complexity": {
            "complexity_score": 10,
            "portability_rating": 100,
            "node_count": 5,
            "join_count": 0,
            "cte_count": 0,
            "subquery_count": 0,
        },
        "lineage": {
            "email_address": {
                "name": "email_address",
                "expression": "u.email",
                "source": "users AS u",
                "downstream": [
                    {
                        "name": "email",
                        "expression": "u.email",
                        "source": "users",
                        "downstream": []
                    }
                ]
            },
            "order_total": {
                "name": "order_total",
                "expression": "o.amount",
                "source": "orders AS o",
                "downstream": [
                    {
                        "name": "amount",
                        "expression": "o.amount",
                        "source": "orders",
                        "downstream": []
                    }
                ]
            }
        }
    }]
    
    annotations = surveyor._create_views_annotations(views_info)
    
    dc_annotations = [ann for ann in annotations if ann.annotation_type == AnnotationType.DATA_CLASS]
    
    assert len(dc_annotations) == 1
    ann = dc_annotations[0]
    assert "PII propagation detected" in ann.summary
    assert "PII" in ann.candidate_data_class_names
    assert "SensitiveData" in ann.candidate_data_class_names
    
    pii_props = ann.json_properties["pii_propagations"]
    assert len(pii_props) == 1
    assert pii_props[0]["column"] == "email_address"
    assert any("users.email" in r for r in pii_props[0]["reasons"])

def test_pii_propagation_dynamic_egeria():
    import os
    from resource_explorer.registry import DatabaseEntity
    from resource_explorer.surveyors.database.database_surveyor import DatabaseSurveyor
    from resource_explorer.surveyors.survey_report import AnnotationType
    
    db_entity = DatabaseEntity(
        slug="test-db",
        display_name="Test DB",
        db_type="postgresql",
        host="localhost",
        port=5432,
        database_name="test-db",
    )
    
    surveyor = DatabaseSurveyor(db_entity, {}, None)
    
    mock_find_response = [
        {
            "properties": {
                "qualifiedName": "ValidValueDefinition::EmailAddressKeyword::custom_secret",
                "displayName": "custom_secret",
                "preferredValue": "custom_secret"
            }
        }
    ]
    
    with mock.patch.dict(os.environ, {
        "EGERIA_PLATFORM_URL": "http://localhost:9443",
        "EGERIA_VIEW_SERVER": "view-server",
        "EGERIA_USER": "steward",
        "EGERIA_USER_PASSWORD": "steward"
    }):
        with mock.patch("pyegeria.omvs.reference_data.ReferenceDataManager") as MockRD:
            instance = MockRD.return_value
            instance.find_valid_value_definitions.return_value = mock_find_response
            
            views_info = [{
                "name": "secret_report",
                "qualified_name": "public.secret_report",
                "dependencies": ["secrets"],
                "complexity": {
                    "complexity_score": 10,
                    "portability_rating": 100,
                    "node_count": 5,
                    "join_count": 0,
                    "cte_count": 0,
                    "subquery_count": 0,
                },
                "lineage": {
                    "custom_secret": {
                        "name": "custom_secret",
                        "expression": "s.val",
                        "source": "secrets AS s",
                        "downstream": [
                            {
                                "name": "val",
                                "expression": "s.val",
                                "source": "secrets",
                                "downstream": []
                            }
                        ]
                    }
                }
            }]
            
            annotations = surveyor._create_views_annotations(views_info)
            
            dc_annotations = [ann for ann in annotations if ann.annotation_type == AnnotationType.DATA_CLASS]
            
            assert len(dc_annotations) == 1
            ann = dc_annotations[0]
            assert "PII propagation detected" in ann.summary
            pii_props = ann.json_properties["pii_propagations"]
            assert len(pii_props) == 1
            assert pii_props[0]["column"] == "custom_secret"


