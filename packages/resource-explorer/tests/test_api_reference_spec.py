"""API reference: parsing a committed OpenAPI/Swagger document into the kind-agnostic interface model."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from resource_explorer.api_reference.model import SOURCE_COMMITTED, Interface
from resource_explorer.api_reference.openapi import load_document, openapi_rejection, parse_openapi

FIX = Path(__file__).parent / "fixtures" / "openapi"


def _load(name):
    return load_document((FIX / name).read_text(), hint=name)


def _op(iface, key):
    return next(o for o in iface.operations if o.key == key)


class TestSwagger2:
    def setup_method(self):
        self.iface = parse_openapi(_load("petstore-swagger2.yaml"), source=SOURCE_COMMITTED, origin="api/swagger.yaml")

    def test_kind_source_and_format(self):
        assert isinstance(self.iface, Interface)
        assert (self.iface.kind, self.iface.source, self.iface.origin) == ("http_api", SOURCE_COMMITTED, "api/swagger.yaml")
        assert self.iface.spec_version == "swagger 2.0" and self.iface.title == "Pet Store" and self.iface.version == "1.0.3"

    def test_operations_with_base_path_kept_apart(self):
        assert sorted(o.key for o in self.iface.operations) == ["GET /pets", "GET /pets/{petId}", "POST /pets"]
        assert self.iface.base_paths == ["/v2"]

    def test_path_level_parameters_merge_and_query_param_fields(self):
        params = {p["name"]: p for p in _op(self.iface, "GET /pets").parameters}
        assert set(params) == {"X-Trace", "limit"}
        assert params["limit"]["in"] == "query" and params["limit"]["type"] == "integer"
        assert params["limit"]["description"] == "Max rows" and params["limit"]["required"] is False

    def test_body_parameter_becomes_request_body(self):
        op = _op(self.iface, "POST /pets")
        assert op.request_body["schema_name"] == "Pet" and op.request_body["required"] is True
        assert all(p["in"] != "body" for p in op.parameters)

    def test_responses_schema_names_and_array(self):
        r = {x["status"]: x for x in _op(self.iface, "GET /pets").responses}
        assert r["200"]["schema_name"] == "Pet[]" and r["default"]["schema_name"] == "Error"

    def test_security_default_override_and_deprecation(self):
        assert _op(self.iface, "GET /pets").security == ["api_key"]
        assert _op(self.iface, "POST /pets").security == []          # explicit `security: []` means none
        assert _op(self.iface, "GET /pets/{petId}").deprecated is True

    def test_tags_summary_operation_id_and_schemas(self):
        op = _op(self.iface, "GET /pets")
        assert (op.tags, op.summary, op.operation_id) == (["pets"], "List pets", "listPets")
        assert set(self.iface.schemas) == {"Pet", "Error"}


class TestOpenApi3:
    def setup_method(self):
        self.iface = parse_openapi(_load("service-openapi3.yaml"), source=SOURCE_COMMITTED, origin="openapi.yaml")

    def test_operations_and_servers(self):
        assert sorted(o.key for o in self.iface.operations) == [
            "DELETE /orders/{orderId}", "GET /health", "GET /orders", "POST /orders"]
        assert self.iface.base_paths == ["/api"] and self.iface.spec_version == "openapi 3.0.3"

    def test_ref_parameter_resolved(self):
        assert [p["name"] for p in _op(self.iface, "GET /orders").parameters] == ["limit"]

    def test_request_body_and_responses(self):
        op = _op(self.iface, "POST /orders")
        assert op.request_body == {"required": True, "content_types": ["application/json"], "schema_name": "NewOrder"}
        r = {x["status"]: x for x in op.responses}
        assert r["201"]["schema_name"] == "Order" and r["422"]["schema_name"] == ""

    def test_security_inherits_and_overrides(self):
        assert _op(self.iface, "GET /orders").security == ["bearerAuth"]
        assert _op(self.iface, "DELETE /orders/{orderId}").security == []

    def test_untagged_operation_has_no_tags(self):
        assert _op(self.iface, "GET /health").tags == []

    def test_tag_descriptions_kept(self):
        assert self.iface.groups == {"orders": "Order handling"}


class TestLiveTestgenSpec:
    """The real running-instance document (TestGen API 5.92.2): 24 paths, 26 operations, 73 schemas, bearer."""

    def setup_method(self):
        self.iface = parse_openapi(_load("testgen-openapi.json"), source="running_instance", origin="http://localhost:8530/api/openapi.json")

    def test_counts(self):
        assert len(self.iface.operations) == 26
        assert len({o.path for o in self.iface.operations}) == 24
        assert len(self.iface.schemas) == 73
        assert self.iface.spec_version == "openapi 3.1.0" and self.iface.version == "5.92.2"

    def test_bearer_security_scheme_listed(self):
        assert self.iface.security_schemes == {"HTTPBearer": "http bearer"}

    def test_form_body_and_ref_response_named(self):
        op = _op(self.iface, "POST /oauth/authorize")
        assert op.request_body["content_types"] == ["application/x-www-form-urlencoded"]
        assert op.request_body["schema_name"]
        assert any(x["schema_name"] == "HTTPValidationError" for x in _op(self.iface, "GET /oauth/authorize").responses)


class TestRejection:
    @pytest.mark.parametrize("doc,needle", [
        ([], "not an object"),
        ({"hello": 1}, "no 'openapi' or 'swagger' key"),
        ({"openapi": "3.0.0"}, "no 'paths'"),
        ({"swagger": "1.2", "paths": {}}, "swagger 1.2"),
        ({"openapi": "4.0.0", "paths": {}}, "openapi 4.0.0"),
    ])
    def test_not_an_openapi_document(self, doc, needle):
        reason = openapi_rejection(doc)
        assert reason and reason.startswith("not an OpenAPI document") and needle in reason

    def test_valid_documents_pass(self):
        assert openapi_rejection(_load("petstore-swagger2.yaml")) is None
        assert openapi_rejection({"openapi": "3.1.0", "paths": {}}) is None

    def test_load_document_json_and_yaml_and_garbage(self):
        assert load_document('{"a": 1}') == {"a": 1}
        assert load_document("a: 1\n") == {"a": 1}
        with pytest.raises(ValueError):
            load_document("<html><body>hi</body></html>")

    def test_operation_cap_is_said_not_silent(self):
        paths = {f"/p{i}": {"get": {"responses": {}}} for i in range(30)}
        iface = parse_openapi({"openapi": "3.0.0", "paths": paths}, source=SOURCE_COMMITTED, max_operations=10)
        assert len(iface.operations) == 10 and iface.total_operations == 30
        assert any("10 of 30" in n for n in iface.notes)

    def test_round_trips_through_json(self):
        iface = parse_openapi(_load("service-openapi3.yaml"), source=SOURCE_COMMITTED, origin="o.yaml")
        again = Interface.from_dict(json.loads(json.dumps(iface.to_dict())))
        assert again.to_dict() == iface.to_dict()
