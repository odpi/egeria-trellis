"""Tests for EgeriaPublisher.publish_sub_resources() — the Egeria-publish
half of the repo scope-narrowing funnel
(docs/repo-scope-narrowing-funnel.md, D2/D3). Covers folder-before-file
ordering, correct parentGUID/parentRelationshipTypeName resolution across
all three edge shapes confirmed live in the sub-resource cataloging design
(docs/assessment-sub-resource-cataloging.md, D5), qualifiedName pinning,
and skip-if-already-found idempotency.

Publish is now driven entirely by the local `sub_resources` registry
table (D2) — never automatically from "worthy" findings — so these tests
mock `registry.list_sub_resources()`, not `registry.query_findings()`."""
from __future__ import annotations

import json
from unittest.mock import MagicMock
from tests.qn_lookup_bridge import expert_from_legacy

from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher

GITHUB_URL = "https://github.com/test/myproj"


def _sub_resource_row(locator, kind, owners=None, **extra):
    detail = {"owners": owners or [], **extra}
    return {
        "locator": locator,
        "kind": kind,
        "cataloged_at": "2026-08-11T00:00:00",
        "source_finding": "repo_sub_resource_survey",
        "detail_json": json.dumps(detail),
        "egeria_guid": "",
    }


def _publisher(registry):
    pub = EgeriaPublisher(platform_url="https://fake", registry=registry)
    pub._automated_curation = MagicMock()
    pub._metadata_expert = expert_from_legacy(pub._automated_curation)
    pub._asset_maker = MagicMock()
    pub._discovery = MagicMock()
    pub._connect = MagicMock()  # publish_sub_resources() connects itself; short-circuit to the mocks above
    # Nothing pre-exists in Egeria by default.
    pub._automated_curation.get_guid_for_name.return_value = "No elements found"   # pyegeria's real miss

    pub._automated_curation.get_template_guid_for_technology_type.side_effect = lambda type_name: f"template-{type_name}"
    counter = {"n": 0}

    def _create(body):
        counter["n"] += 1
        return f"guid-{counter['n']}"

    pub._automated_curation.create_elem_from_template.side_effect = _create
    return pub


class TestReadsFromLocalCatalogOnly:
    def test_no_op_when_locators_not_in_local_catalog(self):
        """publish_sub_resources() only ever publishes rows that are
        already locally catalogued — it never derives from findings
        directly anymore (that's the whole point of decoupling catalog
        from publish, D2/D3)."""
        registry = MagicMock()
        registry.list_sub_resources.return_value = []
        pub = _publisher(registry)
        result = pub.publish_sub_resources("myproj", GITHUB_URL, "asset-guid", ["docs"])
        assert result == {}
        pub._automated_curation.create_elem_from_template.assert_not_called()

    def test_only_requested_locators_are_published_even_if_more_are_catalogued(self):
        registry = MagicMock()
        registry.list_sub_resources.return_value = [
            _sub_resource_row("docs", "folder"),
            _sub_resource_row("README.md", "file"),
        ]
        pub = _publisher(registry)
        result = pub.publish_sub_resources("myproj", GITHUB_URL, "asset-guid", ["docs"])
        assert set(result.keys()) == {"docs"}
        assert pub._automated_curation.create_elem_from_template.call_count == 1


class TestOrderingAndParentResolution:
    def test_root_level_file_parents_to_root_folder_via_nested_file(self):
        registry = MagicMock()
        registry.list_sub_resources.return_value = [
            _sub_resource_row("", "folder"),
            _sub_resource_row("README.md", "file"),
        ]
        pub = _publisher(registry)
        pub.publish_sub_resources("myproj", GITHUB_URL, "asset-guid", ["", "README.md"])

        calls = pub._automated_curation.create_elem_from_template.call_args_list
        assert len(calls) == 2
        folder_body = calls[0].args[0]
        file_body = calls[1].args[0]
        assert folder_body["parentGUID"] == "asset-guid"
        assert folder_body["parentRelationshipTypeName"] == "CapabilityAssetUse"
        assert file_body["parentGUID"] == "guid-1"  # the folder created first
        assert file_body["parentRelationshipTypeName"] == "NestedFile"

    def test_depth_1_folder_parents_directly_to_repo_asset(self):
        registry = MagicMock()
        registry.list_sub_resources.return_value = [_sub_resource_row("docs", "folder")]
        pub = _publisher(registry)
        pub.publish_sub_resources("myproj", GITHUB_URL, "asset-guid", ["docs"])

        body = pub._automated_curation.create_elem_from_template.call_args_list[0].args[0]
        assert body["parentGUID"] == "asset-guid"
        assert body["parentRelationshipTypeName"] == "CapabilityAssetUse"

    def test_subfolder_parents_to_ancestor_folder_via_folder_hierarchy(self):
        registry = MagicMock()
        registry.list_sub_resources.return_value = [
            _sub_resource_row("docs", "folder"),
            _sub_resource_row("docs/guides", "folder"),
        ]
        pub = _publisher(registry)
        pub.publish_sub_resources("myproj", GITHUB_URL, "asset-guid", ["docs", "docs/guides"])

        calls = pub._automated_curation.create_elem_from_template.call_args_list
        sub_body = calls[1].args[0]
        assert sub_body["parentGUID"] == "guid-1"
        assert sub_body["parentRelationshipTypeName"] == "FolderHierarchy"

    def test_nested_file_parents_to_its_containing_folder_via_nested_file(self):
        registry = MagicMock()
        registry.list_sub_resources.return_value = [
            _sub_resource_row("docs", "folder"),
            _sub_resource_row("docs/SECURITY.md", "file"),
        ]
        pub = _publisher(registry)
        pub.publish_sub_resources("myproj", GITHUB_URL, "asset-guid", ["docs", "docs/SECURITY.md"])

        calls = pub._automated_curation.create_elem_from_template.call_args_list
        file_body = calls[1].args[0]
        assert file_body["parentGUID"] == "guid-1"
        assert file_body["parentRelationshipTypeName"] == "NestedFile"

    def test_folders_are_all_created_before_any_file(self):
        registry = MagicMock()
        registry.list_sub_resources.return_value = [
            _sub_resource_row("docs/SECURITY.md", "file"),
            _sub_resource_row("docs", "folder"),
        ]
        pub = _publisher(registry)
        pub.publish_sub_resources("myproj", GITHUB_URL, "asset-guid", ["docs/SECURITY.md", "docs"])

        calls = pub._automated_curation.create_elem_from_template.call_args_list
        assert calls[0].args[0]["placeholderPropertyValues"]["directoryName"] == "docs"
        assert calls[1].args[0]["placeholderPropertyValues"]["fileName"] == "SECURITY.md"

    def test_replacement_properties_pin_the_real_qualified_name(self):
        """Regression guard, confirmed live: without an explicit
        replacementProperties override, Egeria's template mechanism
        derives its own qualifiedName from placeholder values, not our
        qualified_name string byte-for-byte — silently breaking
        find-by-qualifiedName idempotency."""
        registry = MagicMock()
        registry.list_sub_resources.return_value = [_sub_resource_row("docs", "folder")]
        pub = _publisher(registry)
        pub.publish_sub_resources("myproj", GITHUB_URL, "asset-guid", ["docs"])

        body = pub._automated_curation.create_elem_from_template.call_args_list[0].args[0]
        expected_qn = f"GitHubRepository::{GITHUB_URL}::docs"
        assert body["replacementProperties"] == {
            "class": "AssetProperties", "qualifiedName": expected_qn,
            # The naming rule (ruling 2026-10-07): path and repository; MagicMock registry
            # has no project, so the repository reads as the slug.
            "displayName": "docs/ \u00b7 myproj",
        }


class TestIdempotency:
    def test_skips_creation_when_qualified_name_already_found(self):
        registry = MagicMock()
        registry.list_sub_resources.return_value = [_sub_resource_row("docs", "folder")]
        pub = _publisher(registry)
        pub._automated_curation.get_guid_for_name.return_value = [
            "11111111-1111-1111-1111-111111111111"
        ]
        result = pub.publish_sub_resources("myproj", GITHUB_URL, "asset-guid", ["docs"])
        pub._automated_curation.create_elem_from_template.assert_not_called()
        assert result == {"docs": "11111111-1111-1111-1111-111111111111"}
        registry.set_sub_resource_egeria_guid.assert_called_once_with(
            "repo", "myproj", "docs", "11111111-1111-1111-1111-111111111111"
        )

    def test_a_later_entry_can_still_use_the_found_guid_as_its_parent(self):
        registry = MagicMock()
        registry.list_sub_resources.return_value = [
            _sub_resource_row("docs", "folder"),
            _sub_resource_row("docs/SECURITY.md", "file"),
        ]
        pub = _publisher(registry)

        def _lookup(qn):
            if qn.endswith("::docs"):
                return ["22222222-2222-2222-2222-222222222222"]
            return "No elements found"          # pyegeria's real miss ([] is not an answer pyegeria gives)

        pub._automated_curation.get_guid_for_name.side_effect = _lookup
        pub.publish_sub_resources("myproj", GITHUB_URL, "asset-guid", ["docs", "docs/SECURITY.md"])

        # Only the file should have been created via template — the folder
        # was already found.
        calls = pub._automated_curation.create_elem_from_template.call_args_list
        assert len(calls) == 1
        assert calls[0].args[0]["parentGUID"] == "22222222-2222-2222-2222-222222222222"
        assert calls[0].args[0]["parentRelationshipTypeName"] == "NestedFile"

    def test_egeria_guid_written_back_to_registry_on_create(self):
        registry = MagicMock()
        registry.list_sub_resources.return_value = [_sub_resource_row("docs", "folder")]
        pub = _publisher(registry)
        result = pub.publish_sub_resources("myproj", GITHUB_URL, "asset-guid", ["docs"])
        assert result == {"docs": "guid-1"}
        registry.set_sub_resource_egeria_guid.assert_called_once_with("repo", "myproj", "docs", "guid-1")


class TestNoRegistry:
    def test_returns_empty_dict_without_a_registry(self):
        pub = EgeriaPublisher(platform_url="https://fake", registry=None)
        assert pub.publish_sub_resources("myproj", GITHUB_URL, "asset-guid", ["docs"]) == {}
