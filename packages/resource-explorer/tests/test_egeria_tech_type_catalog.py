"""Tests for EgeriaTechTypeCatalog — real Egeria Technology Type catalog reads,
distinct from the RE-specific Survey Definition discovery convention."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from resource_explorer.surveyors.egeria_tech_type_catalog import (
    EgeriaTechTypeCatalog,
    EgeriaTechTypeCatalogError,
)

# Brief I: these tests fake the Egeria clients; the code under test runs as a signed-in caller, as on a route.
pytestmark = pytest.mark.usefixtures("signed_in_caller")


@pytest.fixture
def catalog():
    c = EgeriaTechTypeCatalog(platform_url="https://localhost:9443")
    c._automated_curation = MagicMock()
    return c


class TestListTechnologyTypes:
    def test_caches_wildcard_search(self, catalog):
        catalog._automated_curation.find_technology_types.return_value = [
            {"displayName": "PostgreSQL Relational Database"},
        ]
        first = catalog.list_technology_types()
        second = catalog.list_technology_types()
        assert first == second
        catalog._automated_curation.find_technology_types.assert_called_once()

    def test_refresh_bypasses_cache(self, catalog):
        catalog._automated_curation.find_technology_types.return_value = []
        catalog.list_technology_types()
        catalog.list_technology_types(refresh=True)
        assert catalog._automated_curation.find_technology_types.call_count == 2

    def test_wraps_failures(self, catalog):
        catalog._automated_curation.find_technology_types.side_effect = Exception("boom")
        with pytest.raises(EgeriaTechTypeCatalogError):
            catalog.list_technology_types()


class TestGetTechTypeDetail:
    def test_returns_none_when_not_found(self, catalog):
        catalog._automated_curation.get_tech_type_detail.return_value = "No elements found"
        assert catalog.get_tech_type_detail("PostgreSQL Database") is None

    def test_caches_per_name(self, catalog):
        catalog._automated_curation.get_tech_type_detail.return_value = {"displayName": "x"}
        catalog.get_tech_type_detail("PostgreSQL Relational Database")
        catalog.get_tech_type_detail("PostgreSQL Relational Database")
        catalog._automated_curation.get_tech_type_detail.assert_called_once()


class TestGetProducedAnnotationTypes:
    def test_dedupes_across_resource_list_and_processes(self, catalog):
        catalog._automated_curation.get_tech_type_detail.return_value = {
            "resourceList": [{
                "specification": {"producedAnnotationType": [
                    {"name": "Capture Database Measurements", "description": "d1",
                     "explanation": "e1", "openMetadataTypeName": "ResourceMeasureAnnotation",
                     "analysisStepName": "Profiling Associated Resources"},
                ]},
            }],
            "governanceActionProcesses": [{
                "specification": {"producedAnnotationType": [
                    {"name": "Capture Database Measurements", "description": "duplicate"},
                    {"name": "Capture List of Tables", "description": "d2",
                     "openMetadataTypeName": "ResourceProfileAnnotation"},
                ]},
            }],
        }
        result = catalog.get_produced_annotation_types("PostgreSQL Relational Database")
        names = [r["name"] for r in result]
        assert names == ["Capture Database Measurements", "Capture List of Tables"]
        assert result[0]["annotation_type"] == "ResourceMeasureAnnotation"

    def test_empty_when_tech_type_not_found(self, catalog):
        catalog._automated_curation.get_tech_type_detail.return_value = "No elements found"
        assert catalog.get_produced_annotation_types("Nonexistent") == []
