"""Tests for sub_resource_templates.resolve_technology_type() — the D5a
extension/filename -> Egeria technology-type mapping used to pick the
correct catalog template for a cataloged file asset."""
from __future__ import annotations

import pytest

from resource_explorer.surveyors.sub_resource_templates import (
    DEFAULT_TECHNOLOGY_TYPE,
    _TECH_TYPE_BY_EXTENSION,
    _TECH_TYPE_BY_NAME,
    resolve_technology_type,
)


class TestCompiledLanguageSource:
    def test_java_maps_to_source_code_file(self):
        assert resolve_technology_type("src/Main.java") == "Source Code File"

    def test_go_maps_to_source_code_file(self):
        assert resolve_technology_type("cmd/main.go") == "Source Code File"

    def test_cpp_header_maps_to_source_code_file(self):
        assert resolve_technology_type("include/foo.hpp") == "Source Code File"


class TestInterpretedLanguageScripts:
    def test_python_maps_to_script_file(self):
        assert resolve_technology_type("app/main.py") == "Script File"

    def test_shell_script_maps_to_script_file(self):
        assert resolve_technology_type("scripts/run.sh") == "Script File"

    def test_typescript_maps_to_script_file(self):
        assert resolve_technology_type("src/index.ts") == "Script File"


class TestDocumentation:
    def test_markdown_maps_to_document_file_not_a_markdown_specific_type(self):
        # No "Markdown Document File" template exists on the confirmed-live
        # instance — must fall back to the general "Document File".
        assert resolve_technology_type("README.md") == "Document File"

    def test_txt_maps_to_document_file(self):
        assert resolve_technology_type("NOTES.txt") == "Document File"


class TestStructuredDataDirectMatches:
    def test_json_maps_to_json_data_file(self):
        assert resolve_technology_type("config.json") == "JSON Data File"

    def test_yaml_maps_to_yaml_file(self):
        assert resolve_technology_type("values.yaml") == "YAML File"

    def test_csv_maps_to_csv_data_file(self):
        assert resolve_technology_type("data.csv") == "CSV Data File"

    def test_parquet_maps_to_parquet_data_file(self):
        assert resolve_technology_type("data.parquet") == "Parquet Data File"

    def test_xlsx_maps_to_spreadsheet_data_file(self):
        assert resolve_technology_type("report.xlsx") == "Spreadsheet Data File"

    def test_ipynb_falls_back_to_json_data_file(self):
        # No Jupyter-specific template exists — .ipynb is valid JSON, the
        # closest real match.
        assert resolve_technology_type("notebook.ipynb") == "JSON Data File"


class TestBuildAndConfigFiles:
    def test_dockerfile_by_exact_name_maps_to_build_instruction_file(self):
        assert resolve_technology_type("Dockerfile") == "Build Instruction File"

    def test_makefile_by_exact_name_maps_to_build_instruction_file(self):
        assert resolve_technology_type("Makefile") == "Build Instruction File"

    def test_github_workflow_yaml_maps_to_build_instruction_file(self):
        assert resolve_technology_type(".github/workflows/ci.yml") == "Build Instruction File"

    def test_toml_config_maps_to_properties_file(self):
        assert resolve_technology_type("pyproject.toml") == "Properties File"

    def test_env_file_maps_to_properties_file(self):
        assert resolve_technology_type(".env") == "Properties File"


class TestArchivesAndExecutables:
    def test_zip_maps_to_archive_file(self):
        assert resolve_technology_type("release.zip") == "Archive File"

    def test_so_maps_to_executable_file(self):
        assert resolve_technology_type("libfoo.so") == "Executable File"


class TestFallback:
    def test_unrecognized_extension_falls_back_to_data_file_specifically(self):
        """Regression guard: the fallback must be exactly 'Data File', not
        'File' — 'File' has no registered catalog template on the live
        instance this mapping was verified against (D5a)."""
        assert resolve_technology_type("weird.xyz123") == "Data File"
        assert resolve_technology_type("weird.xyz123") == DEFAULT_TECHNOLOGY_TYPE

    def test_no_extension_at_all_falls_back_to_data_file(self):
        assert resolve_technology_type("LICENSE") == "Data File"

    def test_never_returns_the_bare_generic_file_type(self):
        """'File' (Egeria's most generic type of all) has no template —
        confirmed live it can never be produced by this function."""
        candidates = [
            "a.py", "a.md", "a.json", "a.zip", "a.so", "Dockerfile",
            "a.unknown_ext", "no_extension_at_all",
        ]
        for path in candidates:
            assert resolve_technology_type(path) != "File"


class TestLiveTemplateAvailability:
    """D5a's required live-checked smoke test — a template being silently
    deregistered in a future Egeria upgrade should fail loudly here, not
    as a mystery cataloging failure deep in _catalog_sub_resources()."""

    @pytest.mark.requires_egeria
    def test_every_returnable_technology_type_has_a_registered_template(self):
        import warnings
        warnings.filterwarnings("ignore")
        from pyegeria.omvs.automated_curation import AutomatedCuration
        from resource_explorer.config import get_config

        cfg = get_config().egeria
        ac = AutomatedCuration(cfg.view_server, cfg.platform_url, cfg.user_id, cfg.user_password)
        ac.create_egeria_bearer_token(cfg.user_id, cfg.user_password)

        returnable_types = (
            set(_TECH_TYPE_BY_EXTENSION.values())
            | set(_TECH_TYPE_BY_NAME.values())
            | {DEFAULT_TECHNOLOGY_TYPE}
        )
        failures = []
        for tech_type in sorted(returnable_types):
            try:
                guid = ac.get_template_guid_for_technology_type(tech_type)
                if not guid:
                    failures.append(tech_type)
            except Exception:
                failures.append(tech_type)

        assert not failures, (
            f"These technology types resolve_technology_type() can return no longer "
            f"have a registered catalog template: {failures}"
        )
