"""The deployment reader (the Classic topology renderer's tests went with Classic, 2026-10-21).

Design reference: docs/architecture-recovery-design.md §5.5f. Ports and wires
come from deployment artifacts only — Dockerfile EXPOSE, compose
ports/expose/depends_on, OpenAPI documents.
"""
from __future__ import annotations





class TestReader:
    def test_deployment_name_comes_from_the_directory(self):
        from resource_explorer.surveyors.repo_survey_definition_adapter import _deployment_name
        assert _deployment_name("compose-configs/egeria-quickstart/egeria-quickstart.yaml") == "egeria-quickstart"
        # A bare docker-compose.yml must not collapse six stacks into one name.
        assert _deployment_name("compose-configs/optional/milvus/docker-compose.yml") == "milvus"
