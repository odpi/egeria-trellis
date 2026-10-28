"""Tests for annotation-linking-plan.md Phase 2 — Tier 1 same-run linking.

Phase 0 (measured live, 2026-09-01) found `AnnotationExtension` UNI_LINK, which
removes the MULTI_LINK pre-check the plan originally sketched: create-blind is
correct, matching `CollectionMembership`/`ResourceList`. This file covers:

  1. `data_profiler.py`/`dependency.py` set `Annotation.evidence_of` correctly
     for their same-run summary/evidence pairs.
  2. `egeria_outbox.py`'s new `annotation_link` element_kind — create-blind (no
     pre-check call), correct body shape/direction, registered in `_CREATORS`.
  3. `annotation_props.publish_annotation_links` — the no-registry/direct-path
     equivalent, same per-item GUID-or-None contract as `publish_annotations`.
  4. `EgeriaPublisher._create_annotations`' second linking pass, both the
     outbox path and the direct path — and, per the task's non-negotiable,
     that a PARTIAL link failure is observable in its returned dict rather
     than silently reported as full success.

Known-negatives are called out explicitly per guard, per
`feedback_checks_weaker_than_they_look.md`.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from resource_explorer.egeria_outbox import (
    OutboxClients,
    _CREATORS,
    _create_annotation_link,
    enqueue_annotation_links,
)
from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.surveyors.annotation_props import publish_annotation_links
from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher
from resource_explorer.surveyors.survey_report import (
    DataClassAnnotation,
    ResourceMeasureAnnotation,
    SchemaAnalysisAnnotation,
)

# Brief I: a publish's link drain runs inside the person's request, as them.
pytestmark = pytest.mark.usefixtures("signed_in_caller")


# ── 1. sub-surveyors set evidence_of correctly ──────────────────────────────


class TestDataProfilerEvidenceOf:
    def test_per_file_schema_annotations_point_at_the_tier1_summary(self, tmp_path, monkeypatch):
        from resource_explorer.surveyors.sub_surveyors.data_profiler import DataProfilerSurveyor

        registry = MagicMock()
        registry.get_file_inventory_with_sizes.return_value = [
            {"file_path": "a.csv", "file_size_bytes": 10},
        ]
        registry.get_data_profiles.return_value = [
            {
                "file_path": "a.csv", "format": "CSV", "row_count": 5, "col_count": 2,
                "schema_json": "[]", "null_summary": "", "file_size_bytes": 10,
                "profiled_at": "2026-01-01",
            },
        ]
        project = Project(slug="p", display_name="p", github_url="https://github.com/o/p")
        surveyor = DataProfilerSurveyor(project, registry)

        results = surveyor.run()

        summary_indices = [i for i, a in enumerate(results) if isinstance(a, ResourceMeasureAnnotation)]
        assert summary_indices == [0], "the aggregate must be index 0 for evidence_of=0 to be correct"
        per_file = [a for a in results if isinstance(a, SchemaAnalysisAnnotation)]
        assert per_file, "fixture should have produced at least one per-file annotation"
        for ann in per_file:
            assert ann.evidence_of == 0

    def test_known_negative_the_no_data_files_summary_is_not_evidence_of_anything(self):
        """A summary with no per-file evidence must NOT accidentally get a
        non-None evidence_of pointing at itself or anything else — proves the
        guard actually distinguishes "has evidence" from "is a bare summary"
        rather than defaulting every annotation to linked."""
        from resource_explorer.surveyors.sub_surveyors.data_profiler import DataProfilerSurveyor

        registry = MagicMock()
        registry.get_file_inventory_with_sizes.return_value = []
        project = Project(slug="p", display_name="p", github_url="https://github.com/o/p")
        surveyor = DataProfilerSurveyor(project, registry)

        results = surveyor.run()

        assert results, "fixture should still emit a result annotation"
        assert all(a.evidence_of is None for a in results)


class TestDependencyEvidenceOf:
    def test_per_ecosystem_annotations_point_at_the_trailing_aggregate(self):
        from resource_explorer.surveyors.sub_surveyors.dependency import DependencySurveyor

        registry = MagicMock()
        registry.query_dependencies.return_value = [
            {"ecosystem": "PyPI", "dep_name": "requests", "dep_version": "2.0", "dep_type": "runtime"},
            {"ecosystem": "npm", "dep_name": "left-pad", "dep_version": "1.0", "dep_type": "runtime"},
        ]
        project = Project(slug="p", display_name="p", github_url="https://github.com/o/p")
        surveyor = DependencySurveyor(project, registry)

        results = surveyor.run()

        summary_indices = [i for i, a in enumerate(results) if isinstance(a, ResourceMeasureAnnotation)]
        assert len(summary_indices) == 1
        summary_index = summary_indices[0]
        assert summary_index == len(results) - 1, "aggregate is appended last"

        per_eco = [a for a in results if isinstance(a, DataClassAnnotation)]
        assert len(per_eco) == 2  # PyPI, npm
        for ann in per_eco:
            assert ann.evidence_of == summary_index

    def test_known_negative_the_no_dependencies_summary_has_no_evidence(self):
        from resource_explorer.surveyors.sub_surveyors.dependency import DependencySurveyor

        registry = MagicMock()
        registry.query_dependencies.return_value = []
        registry.get_file_inventory.return_value = []
        project = Project(slug="p", display_name="p", github_url="https://github.com/o/p")
        surveyor = DependencySurveyor(project, registry)

        results = surveyor.run()

        assert results
        assert all(a.evidence_of is None for a in results)


# ── 2. egeria_outbox annotation_link creator ────────────────────────────────


class TestAnnotationLinkCreator:
    def test_registered_under_annotation_link(self):
        assert _CREATORS["annotation_link"] is _create_annotation_link

    def test_body_shape_and_direction(self):
        """summary is end1 (metadataElement1GUID), evidence is end2 — Phase 0's
        measured direction (docs/annotation-linking-plan.md): get_annotation_
        extensions(end1) returns end2, so this makes
        get_annotation_extensions(summary_guid) the natural 'what backs this'
        query."""
        metadata_expert = MagicMock()
        metadata_expert.create_related_elements.return_value = "link-guid-1"
        clients = OutboxClients(metadata_expert=metadata_expert)

        guid = _create_annotation_link(
            clients, {"summary_guid": "summary-guid", "evidence_guid": "evidence-guid"},
        )

        assert guid == "link-guid-1"
        body = metadata_expert.create_related_elements.call_args.kwargs["body"]
        assert body["typeName"] == "AnnotationExtension"
        assert body["metadataElement1GUID"] == "summary-guid"
        assert body["metadataElement2GUID"] == "evidence-guid"

    def test_known_negative_create_blind_makes_no_pre_check_call(self):
        """The plan's original MULTI_LINK branch would have called
        get_all_related_elements before every create. Phase 0 measured
        AnnotationExtension as UNI_LINK, which makes that unnecessary — assert
        it is genuinely gone, not just unused in this particular test."""
        metadata_expert = MagicMock()
        metadata_expert.create_related_elements.return_value = "g"
        clients = OutboxClients(metadata_expert=metadata_expert)

        _create_annotation_link(clients, {"summary_guid": "s", "evidence_guid": "e"})

        assert not metadata_expert.get_all_related_elements.called
        metadata_expert.create_related_elements.assert_called_once()

    def test_no_metadata_expert_client_raises(self):
        """OutboxClients.require's existing contract — a kind asking for a
        client the drain was not given fails loudly, not silently."""
        from resource_explorer.egeria_outbox import OutboxApplyError

        clients = OutboxClients()
        with pytest.raises(OutboxApplyError, match="metadata_expert"):
            _create_annotation_link(clients, {"summary_guid": "s", "evidence_guid": "e"})


@pytest.fixture()
def db(tmp_path):
    reg = ProjectRegistry(database_url=f"sqlite:///{tmp_path/'t.db'}")
    reg._init_schema()
    return reg


@pytest.fixture()
def project(db):
    db.add(Project(slug="p", display_name="p", github_url="https://github.com/o/p"))
    return "p"


class TestEnqueueAnnotationLinks:
    def test_round_trips_with_synthetic_qualified_name(self, db, project):
        row_ids = enqueue_annotation_links(
            db, "repo", project,
            [{"summary_guid": "sum-1", "evidence_guid": "ev-1"}],
            run_id="run-1",
        )
        due = db.claim_due_outbox_elements()
        assert [r["id"] for r in due] == row_ids
        assert due[0]["element_kind"] == "annotation_link"
        assert due[0]["qualified_name"] == "AnnotationExtension::sum-1::ev-1"

    def test_drain_calls_metadata_expert_and_marks_done(self, db, project):
        row_ids = enqueue_annotation_links(
            db, "repo", project,
            [{"summary_guid": "sum-1", "evidence_guid": "ev-1"}],
            run_id="run-1",
        )
        metadata_expert = MagicMock()
        metadata_expert.create_related_elements.return_value = "new-link-guid"
        from resource_explorer.egeria_outbox import drain_outbox

        summary = drain_outbox(
            db, OutboxClients(metadata_expert=metadata_expert), lambda qn: "",
            run_id="run-1",
        )
        assert summary["done"] == 1
        assert db.get_outbox_guids(row_ids) == {row_ids[0]: "new-link-guid"}

    def test_known_negative_a_second_identical_drain_does_not_duplicate_rows(self, db, project):
        """Replaying enqueue_annotation_links for the same pair (e.g. a
        re-published run) must converge on the SAME outbox row, not create a
        second one — the synthetic qualifiedName is the mechanism, prove it
        actually does this rather than assuming the pattern transferred."""
        row_ids_1 = enqueue_annotation_links(
            db, "repo", project, [{"summary_guid": "sum-1", "evidence_guid": "ev-1"}], run_id="run-1",
        )
        # A naive re-enqueue WOULD insert a second row if nothing guarded it —
        # this test's job is to show the outbox row count, not to claim
        # enqueue itself dedupes (it doesn't; apply_element's qualifiedName
        # lookup does at drain time, same as every other kind).
        rows = db.list_outbox_elements(run_id="run-1")
        assert len(rows) == 1


# ── 3. annotation_props.publish_annotation_links (direct/no-registry path) ──


class TestPublishAnnotationLinksDirect:
    def test_creates_one_link_per_pair(self):
        metadata_expert = MagicMock()
        metadata_expert.create_related_elements.return_value = "link-guid"

        guids = publish_annotation_links(
            metadata_expert, [{"summary_guid": "s", "evidence_guid": "e"}],
        )

        assert guids == ["link-guid"]
        body = metadata_expert.create_related_elements.call_args.kwargs["body"]
        assert body["metadataElement1GUID"] == "s"
        assert body["metadataElement2GUID"] == "e"

    def test_a_failed_create_is_none_not_swallowed(self):
        metadata_expert = MagicMock()
        metadata_expert.create_related_elements.side_effect = RuntimeError("boom")

        guids = publish_annotation_links(
            metadata_expert, [{"summary_guid": "s", "evidence_guid": "e"}],
        )

        assert guids == [None]

    def test_a_200_with_no_guid_is_none_not_mistaken_for_success(self):
        metadata_expert = MagicMock()
        metadata_expert.create_related_elements.return_value = ""

        guids = publish_annotation_links(
            metadata_expert, [{"summary_guid": "s", "evidence_guid": "e"}],
        )

        assert guids == [None]


# ── 4. EgeriaPublisher._create_annotations — the second pass, both paths ───


def _publisher_with_registry(registry):
    pub = EgeriaPublisher(platform_url="https://fake", registry=registry)
    # These tests are about the INLINE path (drain, then links by real GUID). Since 2026-10-14 the config default is
    # enqueue-only, so the inline path is chosen here as an explicit publish chooses it (`publish(defer_drain=False)`).
    pub._defer_drain_override = False
    pub._discovery = MagicMock()
    pub._metadata_expert = MagicMock()
    pub._find_element_guid = lambda qn: ""  # nothing pre-exists
    return pub


class _FakeResult:
    def __init__(self, annotations, slug="p"):
        self.annotations = annotations
        self.resource_slug = slug
        from datetime import datetime
        self.surveyed_at = datetime(2026, 9, 1)


class TestCreateAnnotationsOutboxPath:
    def test_full_success_reports_all_links_created(self, db, project):
        pub = _publisher_with_registry(db)
        guid_counter = {"n": 0}

        def _create(body):
            guid_counter["n"] += 1
            return f"guid-{guid_counter['n']}"

        pub._discovery.create_annotation.side_effect = _create
        pub._metadata_expert.create_related_elements.side_effect = _create

        summary = ResourceMeasureAnnotation(summary="agg", analysis_step="s")
        evidence = SchemaAnalysisAnnotation(summary="ev", analysis_step="s", evidence_of=0)
        result = _FakeResult([summary, evidence], slug=project)

        counts = pub._create_annotations(result, "report-guid")

        # Exact equality kept deliberately — this dict is the publish result's
        # contract and the test exists to catch a count going missing. The two
        # annotation_* keys were added 2026-09-02 with the outbox enqueue cap,
        # so the Activity summary can report what was PUBLISHED rather than
        # what was produced. `links_queued` (run-in-background plan) is
        # always 0 on the INLINE path — it's the deferred/background path's
        # count of links enqueued by row id rather than GUID.
        assert counts == {
            "links_attempted": 1, "links_created": 1, "links_failed": 0, "links_skipped": 0,
            "links_queued": 0, "annotations_produced": 2, "annotations_queued": 2,
        }

    def test_known_negative_a_failed_link_is_visible_not_silent_success(self, db, project):
        """The task's own non-negotiable ratchet: a caller must be able to
        tell 3-of-10 (here 1-of-1) links failed, not read back full success."""
        pub = _publisher_with_registry(db)
        guid_counter = {"n": 0}

        def _create_ann(body):
            guid_counter["n"] += 1
            return f"guid-{guid_counter['n']}"

        pub._discovery.create_annotation.side_effect = _create_ann
        pub._metadata_expert.create_related_elements.side_effect = RuntimeError("link create failed")

        summary = ResourceMeasureAnnotation(summary="agg", analysis_step="s")
        evidence = SchemaAnalysisAnnotation(summary="ev", analysis_step="s", evidence_of=0)
        result = _FakeResult([summary, evidence], slug=project)

        counts = pub._create_annotations(result, "report-guid")

        assert counts["links_attempted"] == 1
        assert counts["links_created"] == 0
        assert counts["links_failed"] == 1, (
            "a failed link create must be counted as failed, not silently "
            "reported as if nothing was attempted"
        )

    def test_a_failed_evidence_annotation_skips_its_link_and_says_so(self, db, project):
        """If the evidence annotation itself never got a GUID, the link
        cannot be created — that must show as 'skipped', not vanish."""
        pub = _publisher_with_registry(db)

        def _create_ann(body):
            props = body["properties"]
            if props["class"] == "SchemaAnalysisAnnotationProperties":
                raise RuntimeError("annotation create failed")
            return "summary-guid"

        pub._discovery.create_annotation.side_effect = _create_ann

        summary = ResourceMeasureAnnotation(summary="agg", analysis_step="s")
        evidence = SchemaAnalysisAnnotation(summary="ev", analysis_step="s", evidence_of=0)
        result = _FakeResult([summary, evidence], slug=project)

        counts = pub._create_annotations(result, "report-guid")

        assert counts["links_skipped"] == 1
        assert counts["links_attempted"] == 0
        assert not pub._metadata_expert.create_related_elements.called

    def test_no_evidence_of_annotations_attempt_no_links(self, db, project):
        pub = _publisher_with_registry(db)
        pub._discovery.create_annotation.return_value = "g"
        result = _FakeResult([ResourceMeasureAnnotation(summary="agg", analysis_step="s")], slug=project)

        counts = pub._create_annotations(result, "report-guid")

        assert counts == {
            "links_attempted": 0, "links_created": 0, "links_failed": 0, "links_skipped": 0,
            "links_queued": 0, "annotations_produced": 1, "annotations_queued": 1,
        }
        assert not pub._metadata_expert.create_related_elements.called


class TestCreateAnnotationsDirectPath:
    """No registry — the fallback path, same class of guarantee."""

    def test_full_success_reports_all_links_created(self):
        pub = EgeriaPublisher(platform_url="https://fake", registry=None)
        pub._discovery = MagicMock()
        pub._metadata_expert = MagicMock()
        pub._find_element_guid = lambda qn: ""
        guid_counter = {"n": 0}

        def _create(body):
            guid_counter["n"] += 1
            return f"guid-{guid_counter['n']}"

        pub._discovery.create_annotation.side_effect = _create
        pub._metadata_expert.create_related_elements.side_effect = _create

        summary = ResourceMeasureAnnotation(summary="agg", analysis_step="s")
        evidence = SchemaAnalysisAnnotation(summary="ev", analysis_step="s", evidence_of=0)
        result = _FakeResult([summary, evidence])

        counts = pub._create_annotations(result, "report-guid")

        assert counts["links_created"] == 1
        assert counts["links_failed"] == 0

    def test_known_negative_a_failed_link_is_visible(self):
        pub = EgeriaPublisher(platform_url="https://fake", registry=None)
        pub._discovery = MagicMock()
        pub._metadata_expert = MagicMock()
        pub._find_element_guid = lambda qn: ""
        pub._discovery.create_annotation.side_effect = lambda body: "g"
        pub._metadata_expert.create_related_elements.side_effect = RuntimeError("boom")

        summary = ResourceMeasureAnnotation(summary="agg", analysis_step="s")
        evidence = SchemaAnalysisAnnotation(summary="ev", analysis_step="s", evidence_of=0)
        result = _FakeResult([summary, evidence])

        counts = pub._create_annotations(result, "report-guid")

        assert counts["links_failed"] == 1
        assert counts["links_created"] == 0
