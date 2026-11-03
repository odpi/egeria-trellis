"""The eight steps that read a table someone else filled, and what their zero means.

Backlog item 5 of the step-outcome work: `repo_website_ingestion` adopted the
five-label vocabulary, the arch-recovery steps followed, and the file-inventory
readers were named as the obvious next ones — "the table was empty" is exactly
an `unverified` that currently reads as a confident zero.

It is not a hypothetical. `project_file_inventory` was written only by RAG
ingestion and `refresh_profile` until `repo_file_inventory` existed, and the
org-import path deliberately skips ingestion. A repo imported that way had an
empty inventory permanently, and every reader of it reported a confident
nothing, forever, without raising.

The sharpest case is `DocumentationSurveyor`: half its score comes from the
inventory, so an empty one produced "Documentation quality: Minimal" — a
verdict about a repository nobody had looked at. That test is the reason this
file exists; the rest guard the same shape in its milder forms.
"""
from __future__ import annotations

import pytest

from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.step_outcome import (
    NO_SIGNAL,
    PARTIAL,
    RECOVERED,
    UNVERIFIED,
    from_upstream_table,
)
from resource_explorer.surveyors.file_classifier.file_classifier_surveyor import (
    FileClassifierSurveyor,
)
from resource_explorer.surveyors.sub_surveyors.data_profiler import DataProfilerSurveyor
from resource_explorer.surveyors.sub_surveyors.documentation import DocumentationSurveyor
from resource_explorer.surveyors.sub_surveyors.file_size import FileSizeSurveyor
from resource_explorer.surveyors.sub_surveyors.sub_resource_survey import SubResourceSurveyor


@pytest.fixture
def registry(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "test.db"))


@pytest.fixture
def project(registry):
    p = Project(slug="myproj", display_name="My Project",
                github_url="https://github.com/test/myproj", collections=[])
    registry.add(p)
    return p


def _outcome_of(annotation) -> str:
    return annotation.json_properties.get("outcome", "")


class TestTheDerivation:
    """`from_upstream_table` is the one place the three-way distinction lives."""

    def test_empty_table_is_unverified_not_no_signal(self):
        o = from_upstream_table(0, 0, empty_table_cause="empty_file_inventory",
                                no_match_cause="no_data_files")
        assert o.outcome == UNVERIFIED
        assert o.known_positive is False
        assert o.is_conclusive is False

    def test_rows_present_but_nothing_matched_is_a_provable_zero(self):
        """The non-empty table IS the known-positive: it is the evidence that
        would have surfaced a match had one existed."""
        o = from_upstream_table(120, 0, empty_table_cause="empty_file_inventory",
                                no_match_cause="no_data_files")
        assert o.outcome == NO_SIGNAL
        assert o.known_positive is True
        assert o.detail["rows_available"] == 120

    def test_a_match_is_recovered(self):
        o = from_upstream_table(120, 7, empty_table_cause="x", no_match_cause="y")
        assert o.outcome == RECOVERED
        assert o.detail["matched"] == 7

    def test_it_never_invents_partial(self):
        """Whether a non-zero result is complete is knowledge only the calling
        step has — a scope filter, a fallback source. Handing back `partial`
        from here would be a guess."""
        for available, matched in ((5, 5), (5, 1), (1, 1)):
            assert from_upstream_table(
                available, matched, empty_table_cause="x", no_match_cause="y"
            ).outcome == RECOVERED


class TestDocumentationQualityIsNotAVerdictOnAnEmptyInventory:
    """The reason this whole file exists."""

    def test_empty_inventory_does_not_produce_a_Minimal_verdict(self, registry, project):
        results = DocumentationSurveyor(project, registry).run()
        quality = [r for r in results if "quality" in r.summary.lower()
                   or "not established" in r.summary]
        assert len(quality) == 1
        ann = quality[0]
        assert _outcome_of(ann) == UNVERIFIED
        assert "Minimal" not in ann.summary
        assert ann.candidate_classifications == []
        assert "inventory" in ann.explanation.lower()

    def test_the_persisted_row_agrees_with_the_annotation(self, registry, project):
        """A trend outliving the run must not say "Minimal" where the annotation
        said "not established"."""
        DocumentationSurveyor(project, registry).run()
        rows = registry.query_findings(project.slug, "documentation")
        quality = [r for r in rows if r["check_name"] == "quality_score"]
        assert len(quality) == 1
        assert quality[0]["label"] == "Unverified"

    def test_a_populated_inventory_still_scores_normally(self, registry, project):
        registry.upsert_file_inventory(
            project.slug,
            [("README.md", 100), ("CHANGELOG.md", 50), ("CONTRIBUTING.md", 20),
             ("SECURITY.md", 10), ("src/a.py", 5)])
        results = DocumentationSurveyor(project, registry).run()
        quality = [r for r in results if "Documentation quality:" in r.summary]
        assert len(quality) == 1
        assert _outcome_of(quality[0]) == RECOVERED
        assert quality[0].candidate_classifications != []


class TestZerosThatUsedToBeSilent:

    def test_file_size_says_so_instead_of_returning_nothing(self, registry, project):
        """Previously: log.debug and an empty annotation list, which is
        indistinguishable from the step never having run."""
        results = FileSizeSurveyor(project, registry).run()
        assert len(results) == 1
        assert _outcome_of(results[0]) == UNVERIFIED
        assert "inventory is empty" in results[0].summary

    def test_file_size_records_a_metric_even_when_it_measured_nothing(self, registry, project):
        FileSizeSurveyor(project, registry).run()
        metrics = registry.query_metrics(project.slug, "repo_file_size")
        assert metrics, "a run that measured nothing must still leave a row"

    def test_data_profiler_distinguishes_no_inventory_from_no_data_files(
            self, registry, project):
        empty = DataProfilerSurveyor(project, registry).run()
        # The empty-inventory path keeps its RequestForAction (it asks for a
        # real next step); the outcome rides on the persisted metric, which is
        # the surface that survives the run.
        assert len(empty) == 1
        assert "No file inventory" in empty[0].summary
        rows = registry.query_metrics_history(project.slug, "data_profile", "total_files")
        assert rows, "the unverified zero must still be persisted"

        # An inventory with files, none of them data files: a provable zero.
        registry.upsert_file_inventory(project.slug, [("src/a.py", 10), ("README.md", 5)])
        populated = DataProfilerSurveyor(project, registry).run()
        assert len(populated) == 1
        assert _outcome_of(populated[0]) == NO_SIGNAL
        assert "No data files" in populated[0].summary

    def test_sub_resource_survey_stops_hedging_with_confidence_50(self, registry, project):
        """confidence=50 was the only signal that this was a non-answer, and a
        number is not a vocabulary — it read as a hedged finding about the repo."""
        results = SubResourceSurveyor(project, registry).run()
        assert len(results) == 1
        assert _outcome_of(results[0]) == UNVERIFIED
        assert results[0].confidence == 100


class TestTheClassifierAdmitsWhenItIsGuessing:

    def test_fallback_sources_are_labelled_partial(self, registry, project):
        """With no inventory the classifier falls back to code symbols, manifests
        and indexed documents, then reports "breakdown across N indexed files" in
        the same words it uses for a complete pass."""
        with registry._conn() as conn:
            conn.execute(
                "INSERT INTO project_code_symbols (project_slug, file_path, language, "
                "kind, name, qualified_name, start_line) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (project.slug, "src/a.py", "python", "function", "f", "src.a.f", 1))
        results = FileClassifierSurveyor(project, registry).run()
        measures = [r for r in results if "extension breakdown" in r.summary]
        assert len(measures) == 1
        assert _outcome_of(measures[0]) == PARTIAL
        assert "partial" in measures[0].summary

    def test_a_real_inventory_is_not_partial(self, registry, project):
        registry.upsert_file_inventory(project.slug, [("src/a.py", 10), ("README.md", 5)])
        results = FileClassifierSurveyor(project, registry).run()
        measures = [r for r in results if "extension breakdown" in r.summary]
        assert len(measures) == 1
        assert _outcome_of(measures[0]) == RECOVERED
        assert "partial" not in measures[0].summary

    def test_nothing_at_all_is_not_reported_as_a_survey_error(self, registry, project):
        """An empty inventory is a missing prerequisite, not a fault in the
        classifier — calling it "Survey error in FileClassification" sends the
        reader to debug the wrong thing."""
        results = FileClassifierSurveyor(project, registry).run()
        assert len(results) == 1
        assert "Survey error" not in results[0].summary
        assert _outcome_of(results[0]) == UNVERIFIED


class TestApiStructureSaysWhichNothingItFound:
    """Same shape as the file-inventory readers, one table along:
    project_code_symbols is filled by repo_symbol_extraction, and an empty one
    means "extraction never ran" far more often than "this repo has no code".
    Measured on the live registry 2026-08-22: 13 of 20 repos had a populated
    file inventory and zero symbols — docling with 1,653 files and none."""

    def _seed(self, registry, slug, file_path="mod.py"):
        with registry._conn() as conn:
            conn.execute(
                "INSERT INTO project_code_symbols (project_slug, file_path, language, "
                "kind, name, qualified_name, start_line) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (slug, file_path, "python", "function", "f", "mod.f", 1))

    def test_no_symbols_is_unverified_not_an_empty_card(self, registry, project):
        from resource_explorer.surveyors.sub_surveyors.api_structure import (
            ApiStructureSurveyor,
        )
        results = ApiStructureSurveyor(project, registry).run()
        assert len(results) == 1
        assert _outcome_of(results[0]) == UNVERIFIED
        assert "not analyzed" in results[0].summary

    def test_symbols_present_but_out_of_scope_is_a_provable_zero(self, registry, project):
        """The distinction the unscoped count exists to make: the table has
        rows, so the step demonstrably ran — the scope just excluded them."""
        from resource_explorer.surveyors.sub_surveyors.api_structure import (
            ApiStructureSurveyor,
        )
        self._seed(registry, project.slug, "src/mod.py")
        results = ApiStructureSurveyor(project, registry, scope_locator="docs/").run()
        assert len(results) == 1
        assert _outcome_of(results[0]) == NO_SIGNAL
        assert "scope" in results[0].summary

    def test_symbols_present_is_recovered(self, registry, project):
        from resource_explorer.surveyors.sub_surveyors.api_structure import (
            ApiStructureSurveyor,
        )
        self._seed(registry, project.slug)
        ApiStructureSurveyor(project, registry).run()
        metrics = registry.query_metrics(project.slug, "api_structure")
        assert metrics["symbol_count"] == 1


class TestDependenciesProveTheirOwnZero:
    """The best known-positive in the set, and the worst underlying gap.

    `project_dependencies` is written by IngestionPipeline and by no survey
    step — the same trap file_inventory and code_symbols were in. Measured on
    the live registry 2026-08-22: 3 of 58 resources had any dependency row,
    while the repos with none ship manifests (docling a pyproject.toml, milvus
    Cargo.toml + go.mod + requirements.txt, egeria_git a build.gradle). Every
    one reported nothing, silently.

    So this step does not fall back on "the upstream table has rows". It checks
    for a manifest: present + zero deps is demonstrably wrong (`unverified`);
    absent + zero deps is a real answer (`no_signal`).
    """

    def _run(self, registry, project):
        from resource_explorer.surveyors.sub_surveyors.dependency import DependencySurveyor
        return DependencySurveyor(project, registry).run()

    def test_a_manifest_with_no_dependencies_is_unverified(self, registry, project):
        registry.upsert_file_inventory(project.slug,
                                       [("pyproject.toml", 400), ("src/a.py", 10)])
        results = self._run(registry, project)
        assert len(results) == 1
        assert _outcome_of(results[0]) == UNVERIFIED
        assert "pyproject.toml" in results[0].summary
        assert results[0].json_properties["manifests_present"] == ["pyproject.toml"]

    def test_no_manifest_and_no_dependencies_is_a_provable_zero(self, registry, project):
        """A repo that declares no dependencies is unremarkable — and provable,
        because the inventory shows there was nothing to declare them in."""
        registry.upsert_file_inventory(project.slug, [("README.md", 100)])
        results = self._run(registry, project)
        assert len(results) == 1
        assert _outcome_of(results[0]) == NO_SIGNAL
        assert results[0].json_properties["manifests_present"] == []

    def test_manifests_are_found_at_any_depth(self, registry, project):
        """Monorepos keep them in subdirectories; a root-only check would call
        every monorepo a provable zero."""
        registry.upsert_file_inventory(project.slug, [("packages/web/package.json", 200)])
        results = self._run(registry, project)
        assert _outcome_of(results[0]) == UNVERIFIED

    def test_an_empty_inventory_cannot_prove_a_zero_either(self, registry, project):
        """The subtle one, and it was wrong in the first draft of this code.

        With no inventory we cannot see whether the repo ships a manifest, so
        "it declares no dependencies" is a claim the run has not earned — the
        absence of a manifest is only evidence if we were able to look. The
        first version returned no_signal here, which is the same unearned
        confidence this whole vocabulary exists to prevent, one level up.
        """
        results = self._run(registry, project)
        assert len(results) == 1
        assert _outcome_of(results[0]) == UNVERIFIED
        assert results[0].json_properties["manifests_present"] == []
        assert "inventory is empty" in results[0].summary

    def test_real_dependencies_are_recovered(self, registry, project):
        registry.upsert_dependencies(project.slug, [
            {"dep_name": "requests", "dep_version": "2.0", "ecosystem": "PyPI",
             "dep_type": "runtime", "source_file": "pyproject.toml"},
        ])
        results = self._run(registry, project)
        totals = [r for r in results if "total dependencies" in r.summary]
        assert len(totals) == 1
        assert _outcome_of(totals[0]) == RECOVERED
