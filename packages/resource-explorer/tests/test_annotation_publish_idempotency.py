"""Tests for annotation_props.publish_annotations() — the shared, idempotent
CREATE loop that replaced three near-identical `_create_annotations` copies
in EgeriaPublisher (repo), EgeriaDatabaseSurveyor and EgeriaFileSystemSurveyor.

Design: docs/outbox-publishing-design.md D2. Before this change,
`DataDiscovery.create_annotation` was called blind — no lookup — so replaying
a publish after a crash between Egeria's write and the caller recording
success created a duplicate annotation. The fix mirrors the existing
search-by-qualifiedName-then-create shape already used by
`EgeriaPublisher._find_or_create_asset` and `_find_element_guid`
(sub-resources, D8): look the qualifiedName up first; adopt the GUID if found,
create only if not.

These tests exercise the shared function directly (fast, no per-publisher
wiring needed) and then exercise it once through each of the three real
publisher classes, to guard against a future copy drifting back apart the
way the property-building logic once did (see test_annotation_props.py).
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from resource_explorer.surveyors.annotation_props import publish_annotations
from resource_explorer.surveyors.database.egeria_database_surveyor import EgeriaDatabaseSurveyor
from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher
from resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor import EgeriaFileSystemSurveyor
from resource_explorer.surveyors.survey_report import ResourceMeasureAnnotation

ANNOTATIONS = [
    ResourceMeasureAnnotation(summary="1234 files", analysis_step="a", resource_properties={"n": 1}),
    ResourceMeasureAnnotation(summary="5678 files", analysis_step="b", resource_properties={"n": 2}),
]


class TestPublishAnnotationsShared:
    def test_new_annotation_is_created(self):
        discovery = MagicMock()
        discovery.create_annotation.return_value = "new-guid"
        find_guid = MagicMock(return_value="")  # nothing pre-exists

        publish_annotations(discovery, find_guid, ANNOTATIONS[:1], "report-1", "Annotation::proj::ts")

        discovery.create_annotation.assert_called_once()
        body = discovery.create_annotation.call_args.kwargs["body"]
        assert body["properties"]["qualifiedName"] == "Annotation::proj::ts::0"
        assert body["parentGUID"] == "report-1"
        assert body["parentRelationshipTypeName"] == "ReportedAnnotation"

    def test_existing_annotation_is_not_recreated(self):
        """The core idempotency guard: a qualifiedName that already resolves
        to a GUID must NOT trigger a second create_annotation call."""
        discovery = MagicMock()
        find_guid = MagicMock(return_value="existing-guid-123")

        publish_annotations(discovery, find_guid, ANNOTATIONS[:1], "report-1", "Annotation::proj::ts")

        discovery.create_annotation.assert_not_called()
        find_guid.assert_called_once_with("Annotation::proj::ts::0")

    def test_lookup_is_per_annotation_by_index(self):
        """Each annotation's qualifiedName embeds its own index; a lookup miss
        on annotation 0 must not be assumed for annotation 1 (and vice versa)."""
        discovery = MagicMock()
        # First annotation already exists, second does not.
        find_guid = MagicMock(side_effect=["existing-guid", ""])

        publish_annotations(discovery, find_guid, ANNOTATIONS, "report-1", "Annotation::proj::ts")

        assert find_guid.call_args_list == [
            (("Annotation::proj::ts::0",),),
            (("Annotation::proj::ts::1",),),
        ]
        discovery.create_annotation.assert_called_once()
        created_qn = discovery.create_annotation.call_args.kwargs["body"]["properties"]["qualifiedName"]
        assert created_qn == "Annotation::proj::ts::1"

    def test_lookup_failure_creates_nothing(self):
        """A look-up that could not be made (network blip) is not "not there": the annotation is NOT
        created, and its slot in the result is None (it used to fall through to create; reversed by
        re/lookup-failure-never-creates, AUDIT-PYEGERIA-6.2.2.md "Not in scope")."""
        discovery = MagicMock()

        def _raise(_qn):
            raise RuntimeError("network blip")

        guids = publish_annotations(discovery, _raise, ANNOTATIONS[:1], "report-1", "Annotation::proj::ts")

        discovery.create_annotation.assert_not_called()
        assert guids == [None]

    def test_create_failure_is_logged_and_loop_continues(self):
        """Error-handling semantics unchanged: a failed annotation must not
        abort the remaining annotations in the batch."""
        discovery = MagicMock()
        discovery.create_annotation.side_effect = [RuntimeError("boom"), "guid-2"]
        find_guid = MagicMock(return_value="")

        publish_annotations(discovery, find_guid, ANNOTATIONS, "report-1", "Annotation::proj::ts")

        assert discovery.create_annotation.call_count == 2


def _publisher():
    pub = EgeriaPublisher(platform_url="https://fake")
    pub._discovery = MagicMock()
    pub._find_element_guid = MagicMock(return_value="")
    return pub


def _db_surveyor():
    surveyor = EgeriaDatabaseSurveyor(platform_url="https://fake")
    surveyor._discovery = MagicMock()
    surveyor._find_element_guid = MagicMock(return_value="")
    return surveyor


def _fs_surveyor():
    surveyor = EgeriaFileSystemSurveyor(platform_url="https://fake")
    surveyor._discovery = MagicMock()
    surveyor._find_element_guid = MagicMock(return_value="")
    return surveyor


class TestEachPublisherWiredThroughSharedImplementation:
    """All three callers must actually route through publish_annotations —
    not just produce equivalent output by coincidence."""

    def test_repo_publisher_creates_new_and_skips_existing(self):
        pub = _publisher()
        from resource_explorer.surveyors.survey_report import SurveyResult

        result = SurveyResult(
            resource_slug="myproj",
            project_display_name="My Proj",
            github_url="https://github.com/test/myproj",
            surveyed_at=datetime(2026, 8, 29, 12, 0, 0),
            annotations=ANNOTATIONS,
        )
        pub._create_annotations(result, "report-guid")
        assert pub._discovery.create_annotation.call_count == 2

        # Now simulate a replay: the same run, but Egeria already has both.
        pub2 = _publisher()
        pub2._find_element_guid = MagicMock(return_value="already-there")
        pub2._create_annotations(result, "report-guid")
        pub2._discovery.create_annotation.assert_not_called()

    def test_database_surveyor_idempotent_on_replay(self):
        surveyor = _db_surveyor()
        surveyor._find_element_guid = MagicMock(return_value="already-there")
        surveyor._create_annotations(ANNOTATIONS, "report-guid", "mydb", "2026-08-29T12:00:00")
        surveyor._discovery.create_annotation.assert_not_called()

    def test_database_surveyor_creates_when_absent(self):
        surveyor = _db_surveyor()
        surveyor._create_annotations(ANNOTATIONS, "report-guid", "mydb", "2026-08-29T12:00:00")
        assert surveyor._discovery.create_annotation.call_count == 2
        first_qn = surveyor._discovery.create_annotation.call_args_list[0].kwargs["body"]["properties"]["qualifiedName"]
        assert first_qn == "Annotation::PostgreSQL::mydb::2026-08-29T12:00:00::0"

    def test_filesystem_surveyor_idempotent_on_replay(self):
        """Regression-adjacent: this is the publisher that shipped silently
        broken (see annotation_props.py module docstring). Must both create
        correctly AND be idempotent, same as the other two."""
        surveyor = _fs_surveyor()
        surveyor._find_element_guid = MagicMock(return_value="already-there")
        surveyor._create_annotations(ANNOTATIONS, "report-guid", "myfs", "2026-08-29T12:00:00")
        surveyor._discovery.create_annotation.assert_not_called()

    def test_filesystem_surveyor_creates_when_absent(self):
        surveyor = _fs_surveyor()
        surveyor._create_annotations(ANNOTATIONS, "report-guid", "myfs", "2026-08-29T12:00:00")
        assert surveyor._discovery.create_annotation.call_count == 2
        first_qn = surveyor._discovery.create_annotation.call_args_list[0].kwargs["body"]["properties"]["qualifiedName"]
        assert first_qn == "Annotation::FileSystem::myfs::2026-08-29T12:00:00::0"


class TestGuidCapture:
    """docs/annotation-linking-plan.md Phase 1: publish_annotations() must
    stop discarding create_annotation's return value on the direct/
    no-registry path — see the module's own docstring history and Q1 of the
    plan. These exercise the real function, not a mock's own book-keeping."""

    def test_returns_the_new_guid_in_order(self):
        discovery = MagicMock()
        discovery.create_annotation.side_effect = ["guid-0", "guid-1"]
        find_guid = MagicMock(return_value="")

        guids = publish_annotations(discovery, find_guid, ANNOTATIONS, "report-1", "Annotation::proj::ts")

        assert guids == ["guid-0", "guid-1"]

    def test_returns_the_existing_guid_when_adopted(self):
        """The idempotency-lookup branch must also surface its GUID — a
        caller storing "the GUID for this annotation" needs it whether the
        annotation was just created or already existed."""
        discovery = MagicMock()
        find_guid = MagicMock(return_value="existing-guid-123")

        guids = publish_annotations(discovery, find_guid, ANNOTATIONS[:1], "report-1", "Annotation::proj::ts")

        assert guids == ["existing-guid-123"]

    def test_known_negative_failed_create_returns_none_not_a_fake_guid(self):
        """The other half of the guard: a create that raises must show up as
        None in the returned list, not silently vanish (a caller zipping
        annotations with guids by position would otherwise misalign) and
        never as a fabricated/blank string that could pass a truthiness
        check for "published"."""
        discovery = MagicMock()
        discovery.create_annotation.side_effect = [RuntimeError("boom"), "guid-1"]
        find_guid = MagicMock(return_value="")

        guids = publish_annotations(discovery, find_guid, ANNOTATIONS, "report-1", "Annotation::proj::ts")

        assert guids == [None, "guid-1"]

    def test_known_negative_empty_guid_response_returns_none(self):
        """Q1's documented edge case: a 200 OK whose body has no "guid" key
        makes pyegeria's create_annotation return None with no exception —
        distinct code path from the exception case above, and must ALSO
        surface as None rather than being mistaken for a real (falsy-looking)
        guid."""
        discovery = MagicMock()
        discovery.create_annotation.return_value = None
        find_guid = MagicMock(return_value="")

        guids = publish_annotations(discovery, find_guid, ANNOTATIONS[:1], "report-1", "Annotation::proj::ts")

        assert guids == [None]
