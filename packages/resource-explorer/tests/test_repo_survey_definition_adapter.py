"""Regression coverage for repo_survey_definition_adapter.py's re_analysis_steps
runners after the refactor to delegate to SurveyOrchestrator.run(steps=[...])
instead of each maintaining its own surveyor closure. Public contract must be
unchanged: runner(project, registry, **_) -> {"annotations": [...]}.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from resource_explorer.registry import Project
from resource_explorer.surveyors.repo_survey_definition_adapter import _build_re_analysis_steps


def _project():
    return Project(
        slug="myproj",
        display_name="My Project",
        github_url="https://github.com/test/myproj",
        description="",
    )


def test_each_step_key_delegates_to_orchestrator_with_itself_as_the_only_step():
    steps = _build_re_analysis_steps()
    project = _project()
    registry = MagicMock()

    for key, runner in steps.items():
        fake_result = MagicMock(annotations=[f"ann-for-{key}"], survey_commit_sha="sha1",
                                commit_by_step={key: "sha1"})
        with patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as MockOrch:
            MockOrch.return_value.run.return_value = fake_result
            output = runner(project, registry)

        # fast=False is the runner's own default when its caller (e.g. the
        # Survey Definition executor) doesn't pass one — forwarded
        # unconditionally to SurveyOrchestrator.run(), which itself only
        # actually applies it to steps whose StepInfo.accepts_fast is True.
        MockOrch.return_value.run.assert_called_once_with(project.slug, steps=[key], fast=False)
        # survey_commit_sha: the commit the run read, for the executor's step_runs row (artifacts slice 1).
        assert output == {"annotations": [f"ann-for-{key}"], "survey_commit_sha": "sha1"}


def test_all_step_keys_are_registered():
    """Exhaustive on purpose: adding a step without a runner would otherwise
    surface only as a Survey Definition that silently does less than its
    definition says. Deliberately not named for a count — it was
    "seventeen" until repo_file_inventory made it eighteen."""
    steps = _build_re_analysis_steps()
    assert set(steps.keys()) == {
        "repo_git_statistics", "repo_file_inventory", "repo_file_structure", "repo_file_size", "repo_language",
        "repo_health", "repo_homepage", "repo_website_ingestion", "repo_dependency", "repo_documentation", "repo_security",
        "repo_api_structure", "repo_data_profiling", "repo_file_classification",
        "repo_sub_resource_survey", "repo_license_classification",
        # 2026-09-12: dependency_support — which curated technologies the
        # dependency list indicates, and whether Egeria holds a type for each.
        "repo_dependency_support",
        # 2026-09-14: deployment_evidence / egeria_interfaces — layer-1
        # deployment evidence and consumed-Egeria-interfaces, both zero-fetch
        # ("Cataloguing in layers", project owner decision).
        "repo_deployment_evidence", "repo_egeria_interfaces",
        "repo_security_features", "repo_ci_quality", "repo_maturity",
        "repo_conventions", "repo_symbol_extraction", "repo_rag_ingestion",
        "repo_foss_scorecard", "repo_cve_scan", "repo_community_support", "repo_interface_surface",
        "repo_chaoss_metrics", "repo_cii_badge", "repo_security_summary",
        # The four GAP analyses, registered 2026-09-01
        # (docs/gap-analyses-design.md). Listed explicitly rather than the
        # assertion being loosened: this test is exhaustive ON PURPOSE, because
        # a step without a runner surfaces only as a Survey Definition that
        # silently does less than it claims — and "does less than it claims"
        # with no error is the failure mode this whole family of analyses was
        # commissioned to remove.
        "repo_secret_scan", "repo_telemetry_scan",
        "repo_contribution_provenance", "repo_sla_content",
        "repo_refresh_plan",
        "repo_arch_detect", "repo_arch_coupling", "repo_arch_lens", "repo_arch_summary",
        "repo_manifest_parse", "repo_classification",
    }


def test_file_inventory_runs_before_every_step_that_reads_the_inventory():
    """Ordering is correctness here, not neatness. STEP_REGISTRY order is also
    the order "Repo Full Survey" runs (the "*" sentinel in
    repo_survey_types.csv), so a refresh placed after its consumers would leave
    them reporting the previous extraction while the run looks like a fresh
    profile."""
    from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY

    order = list(STEP_REGISTRY)
    readers = [
        "repo_file_structure", "repo_language", "repo_file_classification",
        "repo_file_size", "repo_documentation", "repo_sub_resource_survey",
    ]
    idx = order.index("repo_file_inventory")
    for r in readers:
        assert idx < order.index(r), f"repo_file_inventory must precede {r}"


def test_rag_ingestion_runs_last():
    """The mirror of the two ordering tests above. STEP_REGISTRY order is "Full
    Survey (all steps)" order, and repo_rag_ingestion is both the most expensive
    step in the set and the only one nothing downstream reads (pgvector is read
    by Chat and the query router, not by other steps) — so it must never delay
    the cheap signals a survey exists to produce."""
    from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY

    assert list(STEP_REGISTRY)[-1] == "repo_rag_ingestion"


def test_rag_ingestion_declares_no_shared_resource():
    """Deliberate divergence from repo_file_inventory/repo_symbol_extraction:
    IncrementalIndexer downloads a zipball only when there are changed files, so
    declaring zipball_root would make the common no-op case pay for a download."""
    from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY

    assert STEP_REGISTRY["repo_rag_ingestion"].requires_resources == {}


def test_file_inventory_declares_the_shared_zipball_resource():
    """Without requires_resources it gets no extraction root at all; with it,
    resolve_resources shares one download across every step that asks."""
    from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY

    assert STEP_REGISTRY["repo_file_inventory"].requires_resources == {
        "zipball_root": "local_path"
    }


class TestStepCostTiers:
    """Step cost tiers plan (docs/step-cost-tiers-plan.md). Exhaustive on
    purpose, same rationale as test_all_step_keys_are_registered above — a
    new step must make a cost decision, not silently inherit the
    StepInfo default."""

    def test_every_step_declares_both_cost_fields(self):
        from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY

        for key, info in STEP_REGISTRY.items():
            assert info.fetch_cost in {"none", "api", "api_heavy", "download"}, key
            assert info.compute_cost in {"low", "medium", "high"}, key

    def test_fetch_cost_is_never_numeric(self):
        """D2/Hazards: the moment these are seconds, they're wrong for every
        repo but the one they were measured on — same rot as run_time."""
        from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY

        for info in STEP_REGISTRY.values():
            assert not info.fetch_cost.isdigit()
            assert not info.compute_cost.isdigit()

    def test_d3_zipball_steps_never_declare_fetch_cost_none(self):
        """D3: fetch_cost must stay consistent with the structural
        requires_resources signal — a step that downloads a zipball cannot
        claim to fetch nothing."""
        from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY

        for key, info in STEP_REGISTRY.items():
            if "zipball_root" in info.requires_resources:
                assert info.fetch_cost != "none", key

    def test_the_zipball_steps_are_exactly_these(self):
        """Deliberately not named for a count. It was named "five" while the set
        already listed six, which is exactly the drift the step-key test above
        renamed itself to avoid."""
        from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY

        zipball_steps = {
            k for k, info in STEP_REGISTRY.items() if "zipball_root" in info.requires_resources
        }
        assert zipball_steps == {
            # repo_homepage left this set (2026-10-14): it reads a handful of small
            # files through the contents API instead, so Scouting can run it.
            "repo_file_inventory", "repo_data_profiling", "repo_symbol_extraction",
            "repo_arch_detect", "repo_arch_coupling",
            # repo_manifest_parse (2026-08-23) — parses dependency manifests, CI
            # workflow content and convention signals from the SAME extraction
            # the steps above share, so it adds no download of its own.
            "repo_manifest_parse",
            # The four GAP analyses (2026-09-01). All read repository CONTENT —
            # committed-credential patterns, telemetry call sites, CONTRIBUTING
            # text, SLA documents — so each genuinely needs the extraction, and
            # each shares the SAME one: resolve_resources dedupes zipball_root
            # across every selected step, so adding four members here costs no
            # additional download.
            #
            # Listed rather than the assertion being widened. A step quietly
            # acquiring a download is a real cost change, and this test exists
            # so it has to be argued for — the same reason the high-compute
            # guard stayed an equality assertion when repo_arch_coupling joined
            # it earlier today.
            "repo_secret_scan", "repo_telemetry_scan",
            "repo_contribution_provenance", "repo_sla_content",
        }

    def test_arch_coupling_needs_BOTH_a_source_tree_and_git_history(self):
        """Coupling draws on two different views of the repo and one artifact
        cannot serve both.

        `git_clone_root` is `--filter=blob:none --no-checkout`, so its root
        contains only `.git` — verified, zero source files. That is correct for
        the CHANGE view (co-change needs history and no blobs) and useless for
        the CODE view (import extraction needs files on disk).

        An earlier version passed the clone alone as `local_path` and read both
        from it. Co-change worked; import extraction silently scanned an empty
        tree and coupling proposed zero components on every real repo — caught
        only when the first live run against egeria-python returned 9
        components, none of them from coupling."""
        from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY

        assert STEP_REGISTRY["repo_arch_coupling"].requires_resources == {
            "zipball_root": "source_path",
            "git_clone_root": "history_path",
        }

    def test_high_compute_cost_is_a_short_deliberate_list(self):
        """D4: compute_cost="high" must stay a decision, not a drift.

        Was `== {"repo_rag_ingestion"}`, and it did its job: it caught
        repo_arch_coupling being raised on 2026-09-01 and forced the change to
        be argued rather than absorbed. Both members are listed with the
        evidence, so a third addition has to come here and say why.

        - repo_rag_ingestion — embeds the repo (the original D4 member).
        - repo_arch_coupling — measured 32 runs: p90 132s against the 60s
          `medium` ceiling, max 600s, median 0 socket connects. Zero connects is
          the trustworthy direction of that counter, so the git-history walk is
          real compute rather than network wait, and `medium` was admitting it
          to runs that had budgeted a minute.
        - repo_secret_scan — shipped `medium` with a VERIFY flag on 2026-09-01
          and its FIRST real run settled it: 277.3s on egeria_git, the slowest
          step in that repo by a factor of five, against the same 60s ceiling.
          4.6x over. Raised the same day it was measured, which is the whole
          point of shipping a tier flagged VERIFY rather than guessing quietly
          and never revisiting.

          Note this test caught it. The tier was changed in the adapter and this
          guard failed, forcing the addition to be argued here rather than
          absorbed — its third catch, after repo_arch_coupling this morning.

        Deliberately still an equality assertion. A `<=` or a membership check
        would let a fourth step in silently, which is the whole failure this
        guards.
        """
        from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY

        high_compute = {k for k, info in STEP_REGISTRY.items() if info.compute_cost == "high"}
        assert high_compute == {"repo_rag_ingestion", "repo_arch_coupling",
                                "repo_secret_scan"}

    def test_git_statistics_is_the_measured_api_heavy_baseline(self):
        """D4: the 430s-against-odpi/egeria measurement this whole plan is
        grounded in."""
        from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY

        assert STEP_REGISTRY["repo_git_statistics"].fetch_cost == "api_heavy"
        assert STEP_REGISTRY["repo_git_statistics"].compute_cost == "low"


#: Catalog `action` values that are not analyses producing findings.
_NON_SURVEY_ACTIONS = frozenset({"publish", "ingest", "profile"})


class TestStageCostConsistency:
    """The stage-assignment connection (docs/step-cost-tiers-plan.md) —
    a question tagged Scouting/Discovery (the two zero/near-zero-fetch
    funnel stages, CLAUDE.md rule 17) must not be answered by a
    survey-action analysis whose run_time is anything but "fast". Uses
    analysis_catalog.yaml's existing run_time, not the new per-step
    fetch_cost/compute_cost fields — deriving an analysis's cost from its
    steps' costs is explicitly out of scope for this plan (D1).

    action != "survey" entries are excluded: egeria_publish (action=
    "publish") is the one hit this check finds without the exclusion, and
    it's a catalog-of-record write, not an analysis — see the plan's note.
    With the exclusion applied, this passes cleanly today and exists to
    catch future drift (a Scouting/Discovery question acquiring an
    expensive answer), not to clean up a present violation.
    """

    _FAST_ONLY_STAGES = {"Scouting", "Discovery"}

    def _analyses_by_id(self):
        import yaml
        from pathlib import Path

        config_path = (
            Path(__file__).parent.parent / "resource_explorer" / "configdata" / "analysis_catalog.yaml"
        )
        raw = yaml.safe_load(config_path.read_text())
        analyses = {}
        for section in ("repo_analyses", "database_analyses", "filesystem_analyses"):
            for entry in raw.get(section) or []:
                analyses[entry["id"]] = entry
        return analyses

    def _questions(self):
        import yaml
        from pathlib import Path

        config_path = (
            Path(__file__).parent.parent / "resource_explorer" / "configdata" / "question_catalog.yaml"
        )
        raw = yaml.safe_load(config_path.read_text())
        return raw.get("repo_questions") or []

    def test_no_fast_stage_question_is_answered_by_a_non_fast_survey_analysis(self):
        analyses = self._analyses_by_id()
        violations = []
        for question in self._questions():
            stage = question["stage"]
            if stage not in self._FAST_ONLY_STAGES:
                continue
            for analysis_id in question["answering"].get("analysis_ids") or []:
                analysis = analyses.get(analysis_id)
                # Skip only genuine non-survey ACTIONS. The test previously
                # skipped anything whose action was not literally "survey",
                # which also skipped the six catalog entries that carry no
                # `action` key at all — ordinary analyses, and the majority of
                # what fast stages actually reference (foss_scorecard,
                # chaoss_metrics, community_support). A slow action-less
                # analysis in a fast stage would have passed unnoticed.
                if analysis is None:
                    continue
                if analysis.get("action") in _NON_SURVEY_ACTIONS:
                    continue
                if analysis.get("run_time") != "fast":
                    violations.append((question["question"], stage, analysis_id, analysis.get("run_time")))
        assert violations == []

    def test_the_action_exclusion_still_has_something_to_exclude(self):
        """Guards the exclusion above against becoming decorative.

        This used to assert that `egeria_publish` was its one live hit, reached
        from the "Analysis/Enrichment" governance question. That is no longer
        reachable: the question named it in PROSE — "plus Curate zone/catalog
        membership (egeria_publish)" — and the generator matched any known id
        anywhere in the note, so an aside became a dispatch target. The
        generator now excludes `action: publish` ids at source, and
        test_catalog_invariants asserts no question can name one again.

        So the assertion is now about the exclusion's own premise rather than a
        specific victim: non-survey actions still exist in the catalog, and no
        question in a fast-only stage reaches one. If either stops being true
        the exclusion is either pointless or hiding a real violation.
        """
        analyses = self._analyses_by_id()
        actions = {a["id"] for a in analyses.values()
                   if a.get("action") in _NON_SURVEY_ACTIONS}
        assert actions, (
            "no non-survey actions left in the catalog — the exclusion in "
            "test_no_fast_stage_question_is_answered_by_a_non_fast_survey_analysis "
            "now excludes nothing and should be removed"
        )
        assert "egeria_publish" in actions

        reached = {a for q in self._questions() if q["stage"] in self._FAST_ONLY_STAGES
                   for a in (q["answering"].get("analysis_ids") or []) if a in actions}
        assert not reached, (
            f"a fast-stage question now reaches non-survey action(s) {reached}; "
            f"the exclusion is silently skipping them rather than judging them"
        )


class TestRunBatch:
    """D1 (docs/survey-tab-unification-plan.md) — repo's own
    ResourceTypeAdapter.run_batch: one SurveyOrchestrator.run(steps=[...])
    call for a whole step_key list, so the executor's grouping (see
    test_survey_definition_executor.py::TestRunBatch) actually gets the
    single-zipball-download win it exists for."""

    def test_calls_orchestrator_once_with_all_step_keys(self):
        from resource_explorer.surveyors.repo_survey_definition_adapter import _run_batch

        project = _project()
        registry = MagicMock()
        fake_result = MagicMock(annotations=["a1", "a2"], errors=[], survey_commit_sha=None, commit_by_step={})
        with patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as MockOrch:
            MockOrch.return_value.run.return_value = fake_result
            output = _run_batch(project, registry, ["repo_language", "repo_file_classification"])

        MockOrch.return_value.run.assert_called_once_with(
            project.slug, steps=["repo_language", "repo_file_classification"], fast=False,
        )
        assert output == {"annotations": ["a1", "a2"], "errors": [], "survey_commit_sha": None}

    def test_forwards_fast_flag(self):
        from resource_explorer.surveyors.repo_survey_definition_adapter import _run_batch

        project = _project()
        registry = MagicMock()
        fake_result = MagicMock(annotations=[], errors=[])
        with patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as MockOrch:
            MockOrch.return_value.run.return_value = fake_result
            _run_batch(project, registry, ["repo_health", "repo_language"], fast=True)

        _, kwargs = MockOrch.return_value.run.call_args
        assert kwargs["fast"] is True

    def test_surfaces_orchestrator_errors(self):
        from resource_explorer.surveyors.repo_survey_definition_adapter import _run_batch

        project = _project()
        registry = MagicMock()
        fake_result = MagicMock(annotations=[], errors=["repo_data_profiling raised unexpectedly: boom"])
        with patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as MockOrch:
            MockOrch.return_value.run.return_value = fake_result
            output = _run_batch(project, registry, ["repo_language", "repo_data_profiling"])

        assert output["errors"] == ["repo_data_profiling raised unexpectedly: boom"]

    def test_registered_on_the_real_adapter(self):
        from resource_explorer.surveyors.repo_survey_definition_adapter import _ADAPTER, _run_batch
        assert _ADAPTER.run_batch is _run_batch

def test_stage_survey_kinds_are_disjoint_where_they_should_be():
    """The automate_full bundle must not be offered as a scouting-tier survey.

    It surfaced on Scouting's Survey tab because the UI never passed the
    survey_kind filter the endpoint has always supported, so every stage listed
    every Survey Definition — and running the 20-step bundle from Scouting then
    deposited Assessment-tier results (security, documentation) that rendered as
    Scouting Results cards.
    """
    import csv
    from pathlib import Path

    csv_path = Path(__file__).resolve().parent.parent / "docs" / "dr-egeria" / "repo_survey_types.csv"
    rows = list(csv.DictReader(csv_path.open(newline="", encoding="utf-8")))
    by_kind = {}
    for r in rows:
        by_kind.setdefault(r["survey_kind"], set()).add(r["survey_group"])

    assert "RepoFullSurvey" in by_kind["automate_full"]
    assert "RepoFullSurvey" not in by_kind.get("scouting", set())
    # the scouting-tier set, all three of them
    assert by_kind["scouting"] == {"RepoCoarseScout", "RepoCoarseProfile", "RepoScoutingSurvey"}
    # Every stage that has survey types of its own now filters to them, so the
    # automate_full bundle cannot reappear in any of them.
    for kind in ("assessment", "analysis", "discovery"):
        assert "RepoFullSurvey" not in by_kind.get(kind, set())


def test_assessment_and_analysis_surveys_are_self_sufficient():
    """Both prefix the prerequisite refresh steps. Every assessment- and
    analysis-tier step reads project_stats, and documentation/data_profiling/
    sub_resource_survey also read project_file_inventory — neither table is
    written by a step in those tiers, so without the prefix the run scores
    whatever an earlier, unrelated survey happened to leave behind. This is the
    same failure repo_git_statistics and repo_file_inventory were created to fix.
    """
    import csv
    from pathlib import Path

    csv_path = Path(__file__).resolve().parent.parent / "docs" / "dr-egeria" / "repo_survey_types.csv"
    rows = list(csv.DictReader(csv_path.open(newline="", encoding="utf-8")))
    for group in ("RepoAssessmentSurvey", "RepoAnalysisSurvey"):
        steps = [r["step_key"] for r in rows if r["survey_group"] == group]
        assert steps, f"{group} has no steps"
        assert steps[0] == "repo_git_statistics", f"{group} must refresh stats first"
        assert steps[1] == "repo_file_inventory", f"{group} must refresh the inventory second"


def test_analysis_survey_carries_the_expensive_steps_and_assessment_only_the_owner_decided_ones():
    """Analysis is deliberately the expensive tier. Assessment was all-low until
    2026-10-02, when the project owner decided it should also include the four
    compliance steps (secret_scan is compute_cost=high, telemetry_scan medium).
    Those two are the ONLY exceptions: any other step above `low` entering
    Assessment is a different decision and fails here. See
    tests/test_assessment_includes_compliance.py for the membership itself."""
    import csv
    from pathlib import Path

    from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY

    csv_path = Path(__file__).resolve().parent.parent / "docs" / "dr-egeria" / "repo_survey_types.csv"
    rows = list(csv.DictReader(csv_path.open(newline="", encoding="utf-8")))

    def costs(group, skip_prereqs=True):
        steps = [r["step_key"] for r in rows if r["survey_group"] == group]
        if skip_prereqs:
            steps = steps[2:]
        return {s: STEP_REGISTRY[s].compute_cost for s in steps}

    assessment = costs("RepoAssessmentSurvey")
    assert {k for k, v in assessment.items() if v != "low"} == {
        "repo_secret_scan", "repo_telemetry_scan"}
    assert "high" in costs("RepoAnalysisSurvey").values()


def test_scouting_survey_covers_every_scouting_tier_step():
    """"Scouting Survey" is the run-everything-Scouting-does option: the union
    of Git Statistics Survey and Coarse Profile Survey, which stay available for
    the narrower cases."""
    import csv
    from pathlib import Path

    csv_path = Path(__file__).resolve().parent.parent / "docs" / "dr-egeria" / "repo_survey_types.csv"
    rows = list(csv.DictReader(csv_path.open(newline="", encoding="utf-8")))
    steps = {g: [r["step_key"] for r in rows if r["survey_group"] == g]
             for g in ("RepoCoarseScout", "RepoCoarseProfile", "RepoScoutingSurvey")}
    union = set(steps["RepoCoarseScout"]) | set(steps["RepoCoarseProfile"])
    assert set(steps["RepoScoutingSurvey"]) == union, (
        f"missing {union - set(steps['RepoScoutingSurvey'])}, "
        f"extra {set(steps['RepoScoutingSurvey']) - union}"
    )
    # prerequisites first — same ordering rule as STEP_REGISTRY
    order = steps["RepoScoutingSurvey"]
    assert order.index("repo_git_statistics") < order.index("repo_health")
    assert order.index("repo_file_inventory") < order.index("repo_file_structure")


# ── website ingestion wired into Analysis (2026-08-20) ───────────────────────


def test_website_ingestion_is_an_analysis_kind_with_results():
    """The step existed in STEP_REGISTRY for a while with no AnalysisKind, which
    made it runnable from code and invisible from the UI — no card, no Results.
    Registration is what makes it reachable, so assert it directly."""
    from resource_explorer.surveyors.repo_survey_definition_adapter import ANALYSIS_KINDS

    kind = ANALYSIS_KINDS["website_ingestion"]
    assert kind.step_keys == ["repo_website_ingestion"]
    assert kind.results is not None
    # 'custom' since 2026-08-25, was 'metrics'. In metrics mode every key became
    # a labelled row, so a repo skipped for a good reason showed `chunks 0` as a
    # peer of `reason self_published` — and 20 of 60 repos are in that state, a
    # third of the corpus reading as a failure it did not have. The reason is
    # not a footnote to the number, it is the answer, so the card leads with it.
    assert kind.results.render == "custom"


def test_website_ingestion_catalog_entry_is_tagged_analysis():
    """A card only appears under the intent its catalog entry names. Tagged
    analysis rather than scouting because the step fetches from the network —
    the collect-vs-reason axis in CLAUDE.md rule 17."""
    from resource_explorer.surveyors.analysis_catalog_reader import get_analyses

    entries = {e["id"]: e for e in get_analyses("repo")}
    assert entries["website_ingestion"]["intent"] == "analysis"


def test_analysis_survey_derives_the_homepage_before_ingesting_the_site():
    """repo_homepage is the only step that writes projects.homepage_url, and
    repo_website_ingestion reads it. Without the prerequisite in the same
    definition, the Analysis Survey would ingest whichever site an earlier,
    unrelated run happened to derive — or silently ingest nothing at all, which
    reports as success. Same implicit-prerequisite shape as the file-inventory
    and git-statistics prefixes this survey already carries."""
    import csv
    from pathlib import Path

    csv_path = (Path(__file__).resolve().parents[1]
                / "docs" / "dr-egeria" / "repo_survey_types.csv")
    with csv_path.open() as fh:
        rows = [r for r in csv.DictReader(fh)
                if r["survey_group"] == "RepoAnalysisSurvey"]

    order = [r["step_key"] for r in sorted(rows, key=lambda r: int(r["step_order"]))]
    assert "repo_website_ingestion" in order
    assert order.index("repo_homepage") < order.index("repo_website_ingestion")
    # The self-published check reads the repo's own file inventory.
    assert order.index("repo_file_inventory") < order.index("repo_website_ingestion")


class TestResourceViews:
    """The perspective distinction, enforced in the plumbing.

    design §4.1 names four perspectives as a modelling concept, and three
    separate bugs lived in the gap below the model — each silent, each
    invisible to unit tests because the objects were built correctly and only
    the wiring was wrong. This is the structural guard for the third: two repo
    resources both yield "a directory" and are not interchangeable.
    """

    def test_providers_declare_what_they_supply(self):
        from resource_explorer.surveyors.repo_survey_definition_adapter import (
            VIEW_HISTORY, VIEW_SOURCE, _resource_providers_for,
        )

        providers = _resource_providers_for(None, None)
        # A zipball has no .git; a --no-checkout clone's root has only .git.
        assert providers["zipball_root"].provides == frozenset({VIEW_SOURCE})
        assert providers["git_clone_root"].provides == frozenset({VIEW_HISTORY})

    def test_every_resource_step_declares_the_view_it_reads(self):
        """A missing declaration is an unchecked assumption, and unchecked
        assumptions are precisely what this mechanism exists to catch — so the
        absence of one is itself a failure, not a default."""
        from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY

        undeclared = {
            step: sorted(set(info.requires_resources) - set(info.requires_views or {}))
            for step, info in STEP_REGISTRY.items()
            if set(info.requires_resources) - set(info.requires_views or {})
        }
        assert undeclared == {}, f"steps using a resource without saying what they read: {undeclared}"

    def test_a_step_reading_source_from_a_history_only_resource_is_rejected(self):
        """The original defect, as a test. repo_arch_coupling once declared
        `{"git_clone_root": "local_path"}` and read import source files from
        it. The clone root contains only `.git`, so extraction scanned an empty
        tree, produced zero edges and proposed zero components — on every repo,
        with no error, through a full test suite and a live run."""
        import copy

        from resource_explorer.surveyors.repo_survey_definition_adapter import (
            STEP_REGISTRY, VIEW_SOURCE, validate_resource_views,
        )

        broken = copy.deepcopy(STEP_REGISTRY)
        broken["repo_arch_coupling"].requires_resources = {"git_clone_root": "local_path"}
        broken["repo_arch_coupling"].requires_views = {"git_clone_root": VIEW_SOURCE}

        problems = validate_resource_views(broken)
        assert problems, "the view check failed to reject a source read from a history-only resource"
        assert "git_clone_root" in problems[0] and "source" in problems[0]

    def test_the_shipped_registry_is_consistent(self):
        from resource_explorer.surveyors.repo_survey_definition_adapter import validate_resource_views

        assert validate_resource_views() == []
