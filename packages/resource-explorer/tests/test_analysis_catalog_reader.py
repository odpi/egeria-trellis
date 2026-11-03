"""Tests for the externalized analysis catalog
(configdata/analysis_catalog.yaml + its loader/reader)."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from resource_explorer.surveyors import analysis_catalog_reader as acr


def setup_function(_fn):
    acr.clear_cache()


ALL_INTENTS = {
    "scouting",
    "assessment",
    "discovery",
    "analysis",
    "enrichment",
    "understanding",
    "curate",
}


class TestLoadCatalog:
    def test_repo_analyses_loaded(self):
        analyses = acr.get_analyses("repo", include_egeria_live=False)
        ids = {a["id"] for a in analyses}
        assert "language_file_classification" in ids
        assert "egeria_publish" in ids

    def test_database_analyses_loaded(self):
        analyses = acr.get_analyses("database", include_egeria_live=False)
        ids = {a["id"] for a in analyses}
        assert "schema_inventory" in ids
        assert "egeria_db_survey" in ids

    def test_filesystem_analyses_loaded(self):
        analyses = acr.get_analyses("filesystem", include_egeria_live=False)
        ids = {a["id"] for a in analyses}
        assert "filesystem_inventory" in ids

    def test_unknown_resource_type_returns_empty(self):
        assert acr.get_analyses("nonexistent", include_egeria_live=False) == []

    def test_returns_plain_dicts_not_dataclasses(self):
        analyses = acr.get_analyses("repo", include_egeria_live=False)
        assert all(isinstance(a, dict) for a in analyses)

    def test_all_intent_values_are_within_the_canonical_seven(self):
        for rtype in ("repo", "database", "filesystem"):
            for a in acr.get_analyses(rtype, include_egeria_live=False):
                assert a["intent"] in ALL_INTENTS, f"{a['id']} has unexpected intent {a['intent']!r}"

    def test_reclassified_entries_moved_to_analysis_intent(self):
        # These were 'assessment' in the old catalog; the intent-shell rewrite
        # reclassifies them to 'analysis' (structural/quantitative, not scored).
        analyses = {a["id"]: a for a in acr.get_analyses("repo", include_egeria_live=False)}
        assert analyses["dependency_analysis"]["intent"] == "analysis"
        assert analyses["data_file_profiling"]["intent"] == "analysis"
        assert analyses["api_structure"]["intent"] == "analysis"

    def test_publish_actions_reclassified_to_curate(self):
        repo = {a["id"]: a for a in acr.get_analyses("repo", include_egeria_live=False)}
        db = {a["id"]: a for a in acr.get_analyses("database", include_egeria_live=False)}
        assert repo["egeria_publish"]["intent"] == "curate"
        assert db["egeria_db_survey"]["intent"] == "curate"


class TestFilterByIntent:
    def test_filters_to_scouting(self):
        analyses = acr.get_analyses("repo", intent="scouting", include_egeria_live=False)
        assert {a["id"] for a in analyses} == {
            "language_file_classification", "repository_health", "repo_profile_refresh",
        }

    def test_filters_to_assessment(self):
        """Assessment keeps what genuinely evaluates against criteria.
        license_classification/maturity/repo_conventions moved to discovery on
        2026-08-20 — they derive from already-collected data with zero new
        fetch, which is Discovery's signature (CLAUDE.md rule 17)."""
        analyses = acr.get_analyses("repo", intent="assessment", include_egeria_live=False)
        ids = {a["id"] for a in analyses}
        assert ids == {
            "security_scan", "documentation_coverage", "security_features", "ci_quality",
            # A reducer over the other security analyses (2026-08-31). Assessment
            # because a verdict against criteria is what it produces — it is the
            # only entry here that measures nothing itself.
            "security_summary",
            # Moved here 2026-08-28. Both report dimensions separately rather
            # than averaging, so neither produces a single score — but each
            # renders a judgement against a named external rubric (CHAOSS; the
            # weakest support dimension), and evaluating against criteria is
            # what this stage is for. Cost did not decide it: community_support
            # fetches nothing and was Discovery on that basis until now.
            "chaoss_metrics", "community_support",
            # Added 2026-08-26: OpenSSF-Scorecard-shaped checks. Assessment
            # because it evaluates against criteria, which is this stage's
            # signature — even though it fetches nothing.
            "foss_scorecard",
            # Added 2026-08-26: OSV.dev advisories over recorded dependencies.
            "cve_scan",
            # Added 2026-08-26: the real OpenSSF Best Practices badge, read
            # from bestpractices.dev. Assessment rather than Discovery — it
            # evaluates against criteria (someone else's, externally held) and
            # it fetches, so neither half of Discovery's signature applies.
            "cii_badge",
        # The four GAP analyses, added 2026-09-01. All intent: assessment —
        # they answer "is this worth pursuing / can this be trusted", which is
        # what the assessment tier is for, rather than "what is in this repo"
        # (scouting) or "how is it built" (analysis).
        "secret_scan", "telemetry_scan", "contribution_provenance", "sla_content",
        }

    # architecture_recovery was assigned intent: discovery 2026-08-22 (the
    # maintainer's explicit ruling, not this test's) even though both of its
    # steps fetch (repo_arch_detect needs a zipball, repo_arch_coupling also
    # a git clone) — a named exception to CLAUDE.md rule 17's zero-fetch
    # signature. RE-RETIERED to analysis 2026-08-30 (Dan: "architecture
    # recovery is an analysis step and belongs there"), so it is gone from
    # this set — not because the earlier ruling was wrong to record, but
    # because the ruling itself changed. See analysis_catalog.yaml's entry
    # for the fuller reasoning (profiled ~110s wait, not "cheap enough to
    # gate the expensive tiers" by any measurement).
    #
    # repo_classification added 2026-08-22. It declares requires_resources={},
    # so it would pass the check below WITHOUT being named here — and that is
    # exactly why it is named here. It reaches GitHub directly (repo tree,
    # README, sibling-repo listing), and letting an empty resource declaration
    # hide a real fetch would turn rule 17's signature into a formality. The
    # rule's actual test is "cheap enough to gate the expensive tiers", which a
    # handful of API calls passes; the honest way to record that is an explicit
    # exception, not silence. Design §5.5b.
    #
    # `architecture_doc_lens` is the SECOND entry here (2026-08-25, was third
    # until architecture_recovery left). It reads the project's architecture
    # document, which is up to MAX_DOC_FILES GitHub calls and frequently
    # against a DIFFERENT repository than the one being surveyed. Recorded
    # rather than absorbed, because the next addition should be a decision
    # about the rule, not another entry in this set.
    DISCOVERY_FETCHES_ANYWAY = {"repo_classification", "architecture_doc_lens"}

    def test_discovery_is_the_zero_fetch_derivation_tier(self):
        """Discovery reasons over what Scouting collected rather than fetching:
        every one of its analyses' steps declares requires_resources={} —
        except the named exception above."""
        from resource_explorer.surveyors.repo_survey_definition_adapter import (
            REPO_ANALYSIS_STEP_MAP, STEP_REGISTRY,
        )

        analyses = acr.get_analyses("repo", intent="discovery", include_egeria_live=False)
        ids = {a["id"] for a in analyses}
        # `architecture_summary` is in the ZERO-fetch set deliberately, unlike
        # `architecture_recovery` (retiered out of discovery entirely
        # 2026-08-30, so it no longer appears in this set at all): its input
        # is another step's output rather than an external resource, which is
        # the shape rule 17's discovery tier describes. It is the first
        # analysis here that consumes findings instead of collecting
        # anything.
        assert ids == {"license_classification", "maturity", "repo_conventions",
                       "repo_classification", "architecture_summary",
                       # community_support was here from 2026-08-26 until
                       # 2026-08-28, when it moved to Assessment: naming the
                       # weakest dimension is a judgement against criteria, and
                       # that signature outranks its zero-fetch cost.
                       "interface_surface",
                       # dependency_support (2026-09-12): the same shape as
                       # architecture_summary — its input is repo_manifest_parse's
                       # project_dependencies rows plus a curated mapping, and it
                       # fetches nothing from the repository. The one Egeria call it
                       # makes is catalog consultation, not acquisition, and it
                       # declares requires_resources={} so the loop below holds.
                       "dependency_support",
                       # deployment_evidence / egeria_interfaces (2026-09-14,
                       # "Cataloguing in layers"): both read only
                       # already-parsed findings/dependencies/symbols/file-
                       # inventory rows and declare requires_resources={},
                       # so the loop below holds for them too.
                       "deployment_evidence", "egeria_interfaces"} \
                       | self.DISCOVERY_FETCHES_ANYWAY
        for aid in ids - self.DISCOVERY_FETCHES_ANYWAY:
            for step in REPO_ANALYSIS_STEP_MAP.get(aid, []):
                assert not (getattr(STEP_REGISTRY[step], "requires_resources", {}) or {}), (
                    f"{step} fetches — it does not belong in the zero-fetch Discovery tier"
                )

    def test_intent_all_returns_everything(self):
        unfiltered = acr.get_analyses("repo", include_egeria_live=False)
        all_intent = acr.get_analyses("repo", intent="all", include_egeria_live=False)
        assert len(unfiltered) == len(all_intent)

    #: ENRICHMENT-E1-CONTEXT-TAB, 2026-09-29 (project owner's amendment to
    #: the designer's reply §0.2): analyses whose prerequisite is a human
    #: input, not a survey read, DO run at the Enrichment stage. Named by id
    #: so a future addition is a deliberate edit here, not a silent pass —
    #: same shape as DISCOVERY_FETCHES_ANYWAY above.
    ENRICHMENT_HUMAN_INPUT_ANALYSES = {
        "preliminary_fit", "doc_source_ingestion", "doc_evidence_check", "semantic_suggestions",
    }

    def test_enrichment_and_automate_have_no_entries_by_design(self):
        """Automate is still empty by design — served by its own
        notification_subscriptions table, not the catalog. `discovery` was in
        this list until 2026-08-20 (real entries — see
        test_discovery_is_the_zero_fetch_derivation_tier); `enrichment` joined
        it, in the other direction, on 2026-09-29: the reply's own §0.2 premise
        ("no analysis runs at the Enrichment stage") was corrected by the
        project owner the same day — a class whose prerequisite is a human
        input DOES run there. The four ids above are that class, every one
        carrying `requires_input` naming what unlocks it. All four are
        database-only today (see analysis_catalog.yaml's own comment), so repo
        and filesystem stay genuinely empty for `enrichment`."""
        for rtype in ("repo", "database", "filesystem"):
            ids = {a["id"] for a in acr.get_analyses(rtype, intent="enrichment", include_egeria_live=False)}
            if rtype == "database":
                assert ids == self.ENRICHMENT_HUMAN_INPUT_ANALYSES
                for a in acr.get_analyses(rtype, intent="enrichment", include_egeria_live=False):
                    assert a["requires_input"], f"{a['id']} is intent:enrichment with no requires_input"
            else:
                assert ids == set()
            assert acr.get_analyses(rtype, intent="automate", include_egeria_live=False) == []


class TestFilterByPerspective:
    def test_filters_to_security(self):
        # Egeria's vocabulary since the unification -- "Security", not "security".
        analyses = acr.get_analyses("database", perspective="Security", include_egeria_live=False)
        ids = {a["id"] for a in analyses}
        assert "privilege_audit" in ids

    def test_perspective_excludes_entries_not_tagged_for_it(self):
        analyses = acr.get_analyses("database", perspective="Data Expert", include_egeria_live=False)
        ids = {a["id"] for a in analyses}
        assert "privilege_audit" not in ids  # tagged 'Security'/'Admin', no 'all'
        assert "schema_inventory" in ids  # tagged 'all'

    def test_the_retired_vocabulary_matches_nothing(self):
        """The old four were renamed, not aliased.

        Leaving them working would let both vocabularies stay in use, which is
        the state the unification existed to end -- and a caller passing the old
        value would get a silently empty result rather than a visible break.
        """
        for retired in ("security", "dba", "steward", "data_scientist"):
            got = acr.get_analyses("database", perspective=retired, include_egeria_live=False)
            # Only entries tagged "all" survive, never one tagged for that lens.
            assert all("all" in a["perspectives"] for a in got), retired

    def test_all_perspective_entries_included_regardless(self):
        analyses = acr.get_analyses("repo", perspective="security", include_egeria_live=False)
        ids = {a["id"] for a in analyses}
        assert "language_file_classification" in ids  # tagged 'all'

    def test_perspective_all_returns_everything(self):
        unfiltered = acr.get_analyses("database", include_egeria_live=False)
        all_persp = acr.get_analyses("database", perspective="all", include_egeria_live=False)
        assert len(unfiltered) == len(all_persp)


class TestListPerspectives:
    def test_returns_distinct_real_perspectives(self):
        perspectives = acr.list_perspectives()
        assert "all" not in perspectives
        # One vocabulary now: Egeria's, from foundations.md.
        assert "Admin" in perspectives
        assert "Security" in perspectives
        assert "Steward" in perspectives
        assert "Data Expert" in perspectives

    def test_every_tag_belongs_to_the_egeria_vocabulary(self):
        """The guard against re-divergence.

        RE previously carried its own four perspectives, so the same word meant
        different things on the Analyses row and the Questions checklist, and
        Egeria's other eight had no local meaning at all. Any new tag has to be
        a real Perspective or this fails.
        """
        assert set(acr.list_perspectives()) <= set(acr.EGERIA_PERSPECTIVES)

    def test_the_vocabulary_matches_what_is_authored_in_egeria(self):
        """EGERIA_PERSPECTIVES is a copy, so it can go stale against its source."""
        import re as _re
        from pathlib import Path as _P

        doc = _P(__file__).resolve().parents[1] / "docs" / "dr-egeria" / "foundations" / "foundations.md"
        authored = set(_re.findall(r"^Perspective::(.+)$", doc.read_text(), _re.M))
        assert authored == set(acr.EGERIA_PERSPECTIVES)

    def test_sorted(self):
        perspectives = acr.list_perspectives()
        assert perspectives == sorted(perspectives)


class TestEgeriaLiveMerge:
    def test_disabled_by_default_flag_returns_local_only(self):
        analyses = acr.get_analyses("database", include_egeria_live=False)
        assert all(not a.get("live_from_egeria") for a in analyses)

    def test_merge_failure_is_fail_soft_and_returns_local_only(self):
        with patch(
            "resource_explorer.surveyors.egeria_tech_type_catalog.EgeriaTechTypeCatalog"
        ) as MockCatalog:
            MockCatalog.return_value.get_produced_annotation_types.side_effect = RuntimeError("no connection")
            analyses = acr.get_analyses("database", include_egeria_live=True)
        # Local entries still present, no exception propagated.
        ids = {a["id"] for a in analyses}
        assert "schema_inventory" in ids
        assert all(not a.get("live_from_egeria") for a in analyses)

    def test_merge_success_appends_egeria_native_entries(self):
        with patch(
            "resource_explorer.surveyors.egeria_tech_type_catalog.EgeriaTechTypeCatalog"
        ) as MockCatalog:
            MockCatalog.return_value.get_produced_annotation_types.return_value = [
                {
                    "name": "Column Data Quality",
                    "description": "Native column-level quality scoring.",
                    "explanation": "",
                    "annotation_type": "DataClassAnnotation",
                    "analysis_step_name": "profile-columns",
                }
            ]
            analyses = acr.get_analyses("database", include_egeria_live=True)
        live = [a for a in analyses if a.get("live_from_egeria")]
        assert len(live) == 1
        assert live[0]["name"] == "Column Data Quality"
        assert live[0]["source"] == "egeria"

    def test_resource_type_without_known_tech_type_gets_no_live_entries(self):
        # 'repo' has no entry in _RESOURCE_TYPE_TO_TECH_TYPE yet.
        analyses = acr.get_analyses("repo", include_egeria_live=True)
        assert all(not a.get("live_from_egeria") for a in analyses)


class TestTargetShape:
    """Repo scope-narrowing funnel plan, D5 — every catalog entry declares
    a target_shape; every live-Egeria-merged entry defaults to the
    conservative whole_resource_only since its real shape isn't known."""

    def test_every_local_entry_has_a_target_shape(self):
        for rtype in ("repo", "database", "filesystem"):
            for a in acr.get_analyses(rtype, include_egeria_live=False):
                assert a["target_shape"] in {
                    "corpus", "single_container", "single_leaf", "whole_resource_only",
                }, a["id"]

    def test_matches_the_grounded_inventory_for_a_sample(self):
        # Regression guard against the target-shape audit going stale —
        # spot-checks a few entries whose shape was specifically corrected
        # or discussed during design, not every single one.
        by_id = {a["id"]: a for a in acr.get_analyses("repo", include_egeria_live=False)}
        assert by_id["security_scan"]["target_shape"] == "whole_resource_only"
        assert by_id["api_structure"]["target_shape"] == "corpus"
        assert by_id["data_file_profiling"]["target_shape"] == "corpus"
        assert by_id["language_file_classification"]["target_shape"] == "whole_resource_only"

        db_by_id = {a["id"]: a for a in acr.get_analyses("database", include_egeria_live=False)}
        assert db_by_id["schema_inventory"]["target_shape"] == "single_container"
        assert db_by_id["egeria_db_survey"]["target_shape"] == "whole_resource_only"

    def test_egeria_live_merged_entry_defaults_to_whole_resource_only(self):
        with patch(
            "resource_explorer.surveyors.egeria_tech_type_catalog.EgeriaTechTypeCatalog"
        ) as MockCatalog:
            MockCatalog.return_value.get_produced_annotation_types.return_value = [
                {
                    "name": "Column Data Quality",
                    "description": "Native column-level quality scoring.",
                    "explanation": "",
                    "annotation_type": "DataClassAnnotation",
                    "analysis_step_name": "profile-columns",
                }
            ]
            analyses = acr.get_analyses("database", include_egeria_live=True)
        live = next(a for a in analyses if a.get("live_from_egeria"))
        assert live["target_shape"] == "whole_resource_only"


class TestShapeCompatibility:
    """D6 — a target_shape only makes sense against certain selected
    sub-resource kinds."""

    def test_corpus_is_compatible_with_anything(self):
        assert acr.is_shape_compatible("corpus", "folder") is True
        assert acr.is_shape_compatible("corpus", "file") is True
        assert acr.is_shape_compatible("corpus", "schema") is True
        assert acr.is_shape_compatible("corpus", "table") is True

    def test_single_container_only_compatible_with_container_kinds(self):
        assert acr.is_shape_compatible("single_container", "folder") is True
        assert acr.is_shape_compatible("single_container", "schema") is True
        assert acr.is_shape_compatible("single_container", "file") is False
        assert acr.is_shape_compatible("single_container", "table") is False

    def test_single_leaf_only_compatible_with_leaf_kinds(self):
        assert acr.is_shape_compatible("single_leaf", "file") is True
        assert acr.is_shape_compatible("single_leaf", "table") is True
        assert acr.is_shape_compatible("single_leaf", "folder") is False

    def test_whole_resource_only_is_never_compatible_with_a_selection(self):
        assert acr.is_shape_compatible("whole_resource_only", "folder") is False
        assert acr.is_shape_compatible("whole_resource_only", "file") is False

    def test_compatible_analyses_filters_the_full_catalog(self):
        # A folder selection should surface api_structure/data_file_profiling
        # (corpus) but never repository_health/security_scan (whole-resource-only).
        ids = {a["id"] for a in acr.compatible_analyses("repo", "folder", include_egeria_live=False)}
        assert "api_structure" in ids
        assert "data_file_profiling" in ids
        assert "repository_health" not in ids
        assert "security_scan" not in ids

    def test_compatible_analyses_for_a_file_selection(self):
        ids = {a["id"] for a in acr.compatible_analyses("repo", "file", include_egeria_live=False)}
        assert "api_structure" in ids  # corpus degrades fine to one file
        assert "repository_health" not in ids


class TestAvailabilityIsDeclaredButGuarded:
    """`availability` stopped deriving from `run_time` on 2026-08-30 (Dan's
    ruling), because the two had come apart.

    §20 of `context-compilation-design.md` argued for deriving it and was right
    while they always agreed: a second hand-maintained column is one more thing
    to keep consistent with the first. `architecture_recovery` is the
    counterexample it did not have — 5.9s of COMPUTE, so `run_time: fast` is
    honest, but a compile running it inline would also pay ACQUISITION (14.4s
    warm, ~30s cold) inside a packer whose §20 says in bold that it "must never
    trigger a survey".

    Declaring it fixes that and reopens §20's real worry: a hand-maintained
    column can drift. These tests are the answer to that worry — the value is
    declared, but it cannot contradict what the steps actually do.
    """

    def _fetching(self, analysis_id):
        from resource_explorer.surveyors.repo_survey_definition_adapter import (
            REPO_ANALYSIS_STEP_MAP,
            STEP_REGISTRY,
        )
        steps = REPO_ANALYSIS_STEP_MAP.get(analysis_id) or []
        needed = set()
        for step in steps:
            info = STEP_REGISTRY.get(step)
            needed |= set(getattr(info, "requires_resources", {}) or {})
        return needed

    def test_nothing_inline_acquires_a_resource(self):
        """The hard safety property. A packer must never trigger a survey, and
        anything declaring a zipball or a clone triggers a download."""
        from resource_explorer.surveyors.analysis_catalog_reader import get_analyses

        offenders = [
            (a["id"], sorted(self._fetching(a["id"])))
            for a in get_analyses("repo", include_egeria_live=False)
            if a["availability"] == "inline" and self._fetching(a["id"])
        ]
        assert offenders == [], (
            "these declare availability: inline but acquire a resource, so a "
            f"context compile would download on its hot path: {offenders}"
        )

    def test_nothing_inline_is_slow_by_its_own_run_time(self):
        """Fetch-free is necessary and not sufficient — `cve_scan` and the
        ingestion steps fetch nothing through the resource mechanism and are
        still minutes-scale work."""
        from resource_explorer.surveyors.analysis_catalog_reader import get_analyses

        offenders = [(a["id"], a["run_time"])
                     for a in get_analyses("repo", include_egeria_live=False)
                     if a["availability"] == "inline" and a["run_time"] != "fast"]
        assert offenders == [], f"inline but not fast: {offenders}"

    def test_architecture_recovery_is_the_case_this_exists_for(self):
        """Pinned by name. It is `run_time: fast` and honestly so — the whole
        point is that this no longer makes it inline."""
        from resource_explorer.surveyors.analysis_catalog_reader import get_analyses

        entry = next(a for a in get_analyses("repo", include_egeria_live=False)
                     if a["id"] == "architecture_recovery")
        assert entry["run_time"] == "fast"
        assert entry["availability"] == "queued"

    def test_an_undeclared_entry_defaults_to_queued(self):
        """Guessing cheap is the dangerous direction. An entry nobody has
        thought about must not license itself onto a hot path by its silence."""
        from resource_explorer.surveyors.analysis_catalog_reader import _entry_from_yaml

        entry = _entry_from_yaml({"id": "x", "name": "X", "run_time": "fast"})
        assert entry.availability == "queued"

    def test_something_is_actually_inline(self):
        """Guards the guards: every assertion above is satisfied by tagging
        nothing at all."""
        from resource_explorer.surveyors.analysis_catalog_reader import get_analyses

        inline = [a["id"] for a in get_analyses("repo", include_egeria_live=False)
                  if a["availability"] == "inline"]
        assert len(inline) > 10, f"only {len(inline)} inline — did the field stop loading?"

    def test_a_fetching_analysis_declares_queued_rather_than_defaulting_to_it(self):
        """"Deliberately queued because it fetches" and "nobody has considered
        this yet" must not look identical in the catalog.

        The default protects the second case. An analysis that acquires a
        resource is queued for a STRUCTURAL reason, and saying so is what stops
        a later reader assuming it was simply overlooked — the same distinction
        `run_scope`/`partial` exist to make elsewhere in this codebase.

        Raised by trellis-chat-panel-1f while verifying the merge, who noticed
        architecture_recovery reached `queued` by falling through.
        """
        import re

        from resource_explorer.surveyors.analysis_catalog_reader import _DEFAULT_CONFIG_PATH

        raw = _DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
        for analysis_id in ("architecture_recovery", "code_symbol_extraction",
                            "manifest_parse", "data_file_profiling"):
            block = re.search(rf"^  - id: {analysis_id}$(.*?)(?=^  - id: |\Z)",
                              raw, re.M | re.S)
            assert block, f"{analysis_id} not found in the catalog"
            assert "availability: queued" in block.group(1), (
                f"{analysis_id} acquires a resource, so it must DECLARE "
                "availability: queued rather than reach it by default"
            )
