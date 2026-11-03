"""One resource-type vocabulary, consumed everywhere it used to be retyped.

`docs/multi-resource-questions-design.md` §1.3 recorded the tuple
`("repo", "database", "filesystem")` re-declared in at least five modules;
§13 Phase 0 item 4 asks for one constant, with `dataset` and `model` added
"now so nothing else hardcodes three".

These tests pin two things a reading of the diff cannot: that the call sites
really read the shared constant (identity, not equality — a re-typed copy
would still compare equal), and that the vocabulary/surveyed split stays
honest in both directions.
"""
from __future__ import annotations

from resource_explorer.resource_types import (
    ALL_RESOURCE_TYPES_MARKER,
    DEFAULT_RESOURCE_TYPE,
    RESOURCE_TYPES,
    SURVEYED_RESOURCE_TYPES,
    parse_resource_types,
)


class TestTheVocabulary:
    def test_the_five_types_the_design_names_are_all_present(self):
        assert set(RESOURCE_TYPES) == {"repo", "database", "filesystem", "dataset", "model", "open_data", "open_data_portal", "portal_directory"}

    def test_dataset_and_model_are_vocabulary_but_not_surveyed(self):
        # Nothing surveys them yet, deliberately (design §13 Phase 0 item 4).
        # If that changes, promote them in resource_types.py -- do not "fix"
        # this test by widening the surveyed list without an adapter.
        assert "dataset" not in SURVEYED_RESOURCE_TYPES
        assert "model" not in SURVEYED_RESOURCE_TYPES

    def test_surveyed_is_a_subset_of_the_vocabulary(self):
        assert set(SURVEYED_RESOURCE_TYPES) <= set(RESOURCE_TYPES)

    def test_every_surveyed_type_has_a_registered_adapter(self):
        """The claim SURVEYED_RESOURCE_TYPES makes, checked against reality."""
        from resource_explorer.surveyors.survey_definition_executor import get_adapter

        for rt in SURVEYED_RESOURCE_TYPES:
            assert get_adapter(rt).entity_type == rt

    def test_the_default_is_in_the_vocabulary(self):
        assert DEFAULT_RESOURCE_TYPE in RESOURCE_TYPES


class TestTheCallSitesReadTheSharedConstant:
    """`is` rather than `==`: a module that re-typed the same three strings
    would pass an equality check while re-introducing exactly the duplication
    this constant removed."""

    def test_batch_io(self):
        from resource_explorer import batch_io

        assert batch_io.RESOURCE_TYPES is SURVEYED_RESOURCE_TYPES

    def test_inventory_export(self):
        from resource_explorer import inventory_export

        assert inventory_export.RESOURCE_KINDS is SURVEYED_RESOURCE_TYPES

    def test_depth_offer(self):
        from resource_explorer.workflows import depth_offer

        assert depth_offer._ALL_RESOURCE_TYPES is SURVEYED_RESOURCE_TYPES

    def test_survey_definition_docs(self):
        from resource_explorer.surveyors import survey_definition_docs

        assert survey_definition_docs._KNOWN_RESOURCE_TYPES == set(SURVEYED_RESOURCE_TYPES)

    def test_no_module_still_types_the_tuple_out(self):
        """The grep that found the duplication, as a test.

        Test files are exempt: a test iterating three types for its own
        fixtures is not a second declaration of the vocabulary.
        """
        from pathlib import Path

        package_root = Path(__file__).resolve().parent.parent / "resource_explorer"
        offenders = []
        for path in package_root.rglob("*.py"):
            if path.name == "resource_types.py":
                continue  # the one place the vocabulary is allowed to be spelled
            for line in path.read_text(encoding="utf-8").splitlines():
                code = line.split("#", 1)[0]  # a comment naming the types is prose
                if '"repo", "database", "filesystem"' in code:
                    offenders.append(str(path.relative_to(package_root)))
                    break
        assert offenders == [], (
            f"{offenders} re-declare the resource-type tuple; import "
            "SURVEYED_RESOURCE_TYPES from resource_explorer.resource_types instead."
        )


class TestParseResourceTypes:
    def test_a_single_type(self):
        assert parse_resource_types("database") == ["database"]

    def test_semicolon_separated_per_the_project_owners_decision(self):
        assert parse_resource_types("database;filesystem") == ["database", "filesystem"]

    def test_whitespace_is_tolerated(self):
        assert parse_resource_types(" database ; filesystem ") == ["database", "filesystem"]

    def test_star_expands_to_the_kinds_with_an_adapter(self):
        # Not the whole vocabulary since 2026-10-22 (owner ruling, review §1.1): dataset and model have no adapter.
        assert parse_resource_types(ALL_RESOURCE_TYPES_MARKER) == list(SURVEYED_RESOURCE_TYPES)
        assert parse_resource_types(ALL_RESOURCE_TYPES_MARKER) != list(RESOURCE_TYPES)

    def test_empty_means_repo_so_a_pre_column_row_does_not_vanish(self):
        assert parse_resource_types("") == [DEFAULT_RESOURCE_TYPE]
        assert parse_resource_types("   ") == [DEFAULT_RESOURCE_TYPE]

    def test_duplicates_collapse(self):
        assert parse_resource_types("repo;repo") == ["repo"]

    def test_an_unknown_type_raises_rather_than_disappearing(self):
        # A typo'd "databse" that silently produced no catalog key would look
        # exactly like a resource type nobody has authored questions for.
        import pytest

        with pytest.raises(ValueError, match="databse"):
            parse_resource_types("databse")
